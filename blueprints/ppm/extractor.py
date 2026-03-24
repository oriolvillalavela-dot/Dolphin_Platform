"""
PPM PDF Extraction Pipeline
============================
6-step deterministic rule-based pipeline for extracting molecule synthesis
status records AND 2D chemical structure images from weekly project PDF reports.

Steps:
  1. Registration & Validation
  2. State tracking (active project/theme context)
  3. Proposal page detection
  4. Bounding-box color detection (PIL HSV analysis)
  5. Molecule ID extraction (text below box) + structure image extraction
  6. Validation & DB storage  (+ AI fallback trigger for flagged pages)

Zero-file-storage guarantee: caller deletes the temp file immediately after
this function returns (success or failure).
"""

from __future__ import annotations

import io
import re
import base64
import colorsys
from dataclasses import dataclass, field
from typing import Optional

try:
    import fitz  # PyMuPDF
except ImportError:
    fitz = None  # type: ignore

try:
    from PIL import Image
except ImportError:
    Image = None  # type: ignore

from .ai_fallback import analyse_page_with_ai


# ── Constants ──────────────────────────────────────────────────────────────

_REQUIRED_KEYWORDS = ["Project:", "Theme:", "Proposal"]

_DPI    = 150
_MATRIX = None  # built lazily

_DATE_PATTERNS = [
    re.compile(r"\bW\d{1,2}\s+\d{4}\b"),
    re.compile(r"\b\d{4}-\d{2}-\d{2}\b"),
    re.compile(r"\b\d{2}/\d{2}/\d{4}\b"),
    re.compile(r"\b(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\w*\s+\d{4}\b", re.IGNORECASE),
]

_MOL_ID_RE  = re.compile(r"\b([A-Z]{1,4}-[A-Z0-9]{1,4}-\d{3,6})\b", re.IGNORECASE)
_PROJECT_RE = re.compile(r"Project\s*:\s*(\S+)", re.IGNORECASE)
_THEME_RE   = re.compile(r"Theme\s*:\s*(\S+)", re.IGNORECASE)

# Status HSV colour ranges: (h_min, h_max, s_min, s_max, v_min, v_max, wrap, label)
_COLOR_STATUS_MAP = [
    (175, 210,  35, 100,  45, 100, False, "In plan"),
    ( 40,  70,  55, 100,  60, 100, False, "In progress"),
    ( 90, 165,  45, 100,  35, 100, False, "Obtained"),
    (205, 265,  45, 100,   5,  60, False, "Delivered"),
    (265, 320,  25, 100,  25, 100, False, "On hold"),
    (340, 360,  55, 100,  40, 100,  True, "Cancelled/Stopped"),
    (  0,  18,  55, 100,  40, 100, False, "Cancelled/Stopped"),
]

_MIN_PIXEL_VOTES    = 40
_TEXT_SEARCH_BELOW  = 80   # pts below box bottom

# Structure image search parameters
_STRUCT_SEARCH_X_OFFSET = -10    # pts left of box to start
_STRUCT_SEARCH_WIDTH    = 220    # pts width of search area
_STRUCT_SEARCH_Y_PAD    = 30     # pts above/below box
_STRUCT_MIN_SIZE        = 35     # min px dimension to be a real structure
_STRUCT_MAX_SIZE        = 320    # max px dimension to skip backgrounds
_STRUCT_RENDER_ZOOM     = 2.5    # render quality for clip extraction
_STRUCT_MIN_DARK_PIXELS = 10     # non-white pixels to confirm content


@dataclass
class ExtractionResult:
    records: list[dict] = field(default_factory=list)
    flagged_pages: list[int] = field(default_factory=list)
    week_date: Optional[str] = None
    project_ids_seen: set[str] = field(default_factory=set)


class ValidationError(ValueError):
    """Raised when the uploaded document fails format validation."""


# ── Helpers ─────────────────────────────────────────────────────────────────

def _get_matrix():
    global _MATRIX
    if _MATRIX is None:
        zoom = _DPI / 72.0
        _MATRIX = fitz.Matrix(zoom, zoom)
    return _MATRIX


def _pixel_to_status(r: int, g: int, b: int) -> Optional[str]:
    rf, gf, bf = r / 255.0, g / 255.0, b / 255.0
    h, s, v = colorsys.rgb_to_hsv(rf, gf, bf)
    h_deg, s_pct, v_pct = h * 360, s * 100, v * 100
    for h_min, h_max, s_min, s_max, v_min, v_max, _wrap, status in _COLOR_STATUS_MAP:
        if h_min <= h_deg <= h_max and s_min <= s_pct <= s_max and v_min <= v_pct <= v_max:
            return status
    return None


def _sample_box_border(img: "Image.Image", rect_px: tuple, border_w: int = 6) -> Optional[str]:
    x0, y0, x1, y1 = [int(v) for v in rect_px]
    W, H = img.size
    x0, y0 = max(0, x0), max(0, y0)
    x1, y1 = min(W - 1, x1), min(H - 1, y1)
    if x1 - x0 < 8 or y1 - y0 < 8:
        return None
    votes: dict[str, int] = {}
    try:
        pixels = img.load()
    except Exception:
        return None

    def _vote(px, py):
        if 0 <= px < W and 0 <= py < H:
            p = pixels[px, py]
            st = _pixel_to_status(p[0], p[1], p[2])
            if st:
                votes[st] = votes.get(st, 0) + 1

    for x in range(x0, x1, 2):
        for dy in range(border_w):
            _vote(x, y0 + dy); _vote(x, y1 - dy)
    for y in range(y0, y1, 2):
        for dx in range(border_w):
            _vote(x0 + dx, y); _vote(x1 - dx, y)

    if not votes:
        return None
    best, count = max(votes.items(), key=lambda kv: kv[1])
    return best if count >= _MIN_PIXEL_VOTES else None


def _extract_date_from_text(text: str) -> Optional[str]:
    for pat in _DATE_PATTERNS:
        m = pat.search(text)
        if m:
            return m.group(0).strip()
    return None


def _find_molecule_below(page: "fitz.Page", rect: "fitz.Rect") -> Optional[str]:
    search_area  = fitz.Rect(rect.x0 - 20, rect.y1, rect.x1 + 20, rect.y1 + _TEXT_SEARCH_BELOW)
    inside_area  = fitz.Rect(rect.x0 - 5,  rect.y0 - 5, rect.x1 + 5, rect.y1 + 5)
    for area in (search_area, inside_area):
        blocks = page.get_text("dict", clip=area)["blocks"]
        for block in blocks:
            for line in block.get("lines", []):
                for span in line.get("spans", []):
                    m = _MOL_ID_RE.search(span.get("text", ""))
                    if m:
                        return m.group(1).upper()
    return None


# ── 2D Structure Image Extraction ────────────────────────────────────────────

def _extract_structure_image(doc: "fitz.Document", page: "fitz.Page", mol_rect: "fitz.Rect") -> Optional[str]:
    """
    Attempt to extract a 2D chemical structure image associated with a molecule box.

    Strategy 1: Scan embedded raster images on the page for one that is:
      - Square-ish (aspect 0.4–2.5)
      - Within 200 pts of the molecule box centre
      - Between _STRUCT_MIN_SIZE and _STRUCT_MAX_SIZE px in each dimension

    Strategy 2 (fallback): Render a clip rectangle from the area to the right of the
      molecule box (where structure drawings are commonly positioned in report layouts).
      Reject renders that are effectively blank (all-white).

    Returns base64-encoded PNG string, or None.
    """
    mol_cx = (mol_rect.x0 + mol_rect.x1) / 2
    mol_cy = (mol_rect.y0 + mol_rect.y1) / 2
    page_rect = page.rect

    # ── Strategy 1: Embedded raster images ───────────────────────────────
    try:
        img_infos = page.get_image_info(xrefs=True)
    except Exception:
        img_infos = []

    best_xref = None
    best_dist = float("inf")

    for info in img_infos:
        bbox   = fitz.Rect(info.get("bbox", [0, 0, 0, 0]))
        w, h   = bbox.width, bbox.height
        if w < _STRUCT_MIN_SIZE or h < _STRUCT_MIN_SIZE:
            continue
        if w > _STRUCT_MAX_SIZE or h > _STRUCT_MAX_SIZE:
            continue
        aspect = w / h if h > 0 else 0
        if aspect < 0.4 or aspect > 2.5:
            continue

        img_cx = (bbox.x0 + bbox.x1) / 2
        img_cy = (bbox.y0 + bbox.y1) / 2
        dist   = ((img_cx - mol_cx) ** 2 + (img_cy - mol_cy) ** 2) ** 0.5

        if dist < 200 and dist < best_dist:
            best_dist = dist
            best_xref = info.get("xref")

    if best_xref:
        try:
            pix = fitz.Pixmap(doc, best_xref)
            # Convert CMYK to RGB if needed
            if pix.colorspace and pix.n > 3:
                pix = fitz.Pixmap(fitz.csRGB, pix)
            png_bytes = pix.tobytes("png")
            return base64.b64encode(png_bytes).decode("utf-8")
        except Exception:
            pass

    # ── Strategy 2: Clip-region render (handles vector-drawn structures) ─
    # Search area: to the right of the molecule box, and a bit above/below it.
    # Also try directly below the molecule ID text.
    search_regions = [
        # Right of box (most common in tabular layouts)
        fitz.Rect(
            mol_rect.x1 + 5,
            mol_rect.y0 - _STRUCT_SEARCH_Y_PAD,
            mol_rect.x1 + _STRUCT_SEARCH_WIDTH,
            mol_rect.y1 + _STRUCT_SEARCH_Y_PAD,
        ),
        # Directly below the box (grid layouts)
        fitz.Rect(
            mol_rect.x0 - 20,
            mol_rect.y1 + _TEXT_SEARCH_BELOW,
            mol_rect.x1 + 20,
            mol_rect.y1 + _TEXT_SEARCH_BELOW + 160,
        ),
    ]

    for clip in search_regions:
        # Clamp to page bounds
        clip = clip & page_rect
        if clip.width < _STRUCT_MIN_SIZE or clip.height < _STRUCT_MIN_SIZE:
            continue
        try:
            mat = fitz.Matrix(_STRUCT_RENDER_ZOOM, _STRUCT_RENDER_ZOOM)
            pix = page.get_pixmap(matrix=mat, clip=clip, alpha=False, colorspace=fitz.csRGB)
            png_bytes = pix.tobytes("png")

            # Reject blank/near-white regions
            img = Image.open(io.BytesIO(png_bytes)).convert("RGB")
            sample = list(img.getdata())[:800]
            dark_count = sum(1 for p in sample if min(p) < 220)
            if dark_count < _STRUCT_MIN_DARK_PIXELS:
                continue

            return base64.b64encode(png_bytes).decode("utf-8")
        except Exception:
            continue

    return None


# ── MolScribe SMILES Prediction ────────────────────────────────────────────

def _run_molscribe(png_b64: str) -> Optional[str]:
    """
    Attempt SMILES prediction from a base64-encoded PNG using MolScribe.
    Returns SMILES string or None. Never raises — failure is silent.
    """
    try:
        import base64 as _b64
        from .molscribe_runner import predict_smiles
        png_bytes = _b64.b64decode(png_b64)
        return predict_smiles(png_bytes)
    except Exception:
        return None


# ── Drawing detection ────────────────────────────────────────────────────────

def _detect_colored_rects(page: "fitz.Page", img: "Image.Image") -> list[tuple["fitz.Rect", str]]:
    results: list[tuple[fitz.Rect, str]] = []
    zoom = _DPI / 72.0
    for path in page.get_drawings():
        if not path.get("stroke_color"):
            continue
        rect = path.get("rect")
        if not rect:
            continue
        rect = fitz.Rect(rect)
        if rect.width < 30 or rect.height < 20 or rect.width > 400 or rect.height > 150:
            continue
        px_rect = (rect.x0 * zoom, rect.y0 * zoom, rect.x1 * zoom, rect.y1 * zoom)
        status = _sample_box_border(img, px_rect)
        if not status:
            sc = path["stroke_color"]
            if len(sc) >= 3:
                status = _pixel_to_status(int(sc[0]*255), int(sc[1]*255), int(sc[2]*255))
        if status:
            results.append((rect, status))
    return results


# ── Main Pipeline ────────────────────────────────────────────────────────────

def run_pipeline(
    pdf_path: str,
    job_id: str,
    week_date_override: Optional[str] = None,
    source_filename: Optional[str] = None,
) -> ExtractionResult:
    """
    Execute the full 6-step extraction pipeline on a PDF file.
    Returns ExtractionResult with records (each containing an optional
    structure_img base64 PNG) and flagged page numbers.
    """
    if fitz is None:
        raise RuntimeError("PyMuPDF (pymupdf) is not installed.")
    if Image is None:
        raise RuntimeError("Pillow is not installed.")

    # ── STEP 1: Validation ────────────────────────────────────────────────
    try:
        doc = fitz.open(pdf_path)
    except Exception as exc:
        raise ValidationError(f"Cannot open PDF: {exc}") from exc

    if doc.page_count == 0:
        raise ValidationError("PDF has no pages.")

    full_text = "".join(doc[pg].get_text() for pg in range(doc.page_count))
    missing = [kw for kw in _REQUIRED_KEYWORDS if kw not in full_text]
    if missing:
        raise ValidationError(f"Missing required keywords: {missing}")

    result = ExtractionResult()
    active_project: Optional[str] = None
    active_theme:   Optional[str] = None
    active_week:    Optional[str] = week_date_override

    for page_no in range(doc.page_count):
        page       = doc[page_no]
        page_text  = page.get_text()
        page_1base = page_no + 1

        # ── STEP 2: State tracking ────────────────────────────────────────
        proj_match  = _PROJECT_RE.search(page_text)
        theme_match = _THEME_RE.search(page_text)
        if proj_match and theme_match:
            active_project = proj_match.group(1).strip()
            active_theme   = theme_match.group(1).strip()
            if not active_week:
                active_week = _extract_date_from_text(page_text)
            continue

        # ── STEP 3: Proposal page detection ──────────────────────────────
        is_proposal = False
        if "Proposal" in page_text or "proposal" in page_text:
            date_hint = _extract_date_from_text(page_text)
            if date_hint:
                is_proposal = True
                if not active_week:
                    active_week = date_hint
            else:
                for block in page.get_text("dict")["blocks"]:
                    for line in block.get("lines", []):
                        for span in line.get("spans", []):
                            if span.get("size", 0) >= 12 and "Proposal" in span.get("text", ""):
                                is_proposal = True

        if not is_proposal:
            continue

        if active_project is None or active_theme is None:
            result.flagged_pages.append(page_1base)
            continue

        # Render page thumbnail for colour analysis
        try:
            pix      = page.get_pixmap(matrix=_get_matrix(), alpha=False)
            img      = Image.open(io.BytesIO(pix.tobytes("png"))).convert("RGB")
        except Exception:
            result.flagged_pages.append(page_1base)
            continue

        # ── STEP 4 & 5: Colour + molecule ID + structure image ────────────
        colored_rects = _detect_colored_rects(page, img)
        page_records: list[dict] = []
        page_ambiguous = False

        for rect, status in colored_rects:
            mol_id = _find_molecule_below(page, rect)
            if not mol_id:
                page_ambiguous = True
                break

            # Extract 2D structure image for this molecule box
            struct_img = _extract_structure_image(doc, page, rect)

            # Run MolScribe on the extracted PNG to auto-predict SMILES
            smiles = _run_molscribe(struct_img) if struct_img else None

            page_records.append({
                "project_id":   active_project,
                "theme_id":     active_theme,
                "molecule_id":  mol_id,
                "status":       status,
                "week_date":    active_week or "",
                "page_number":  page_1base,
                "structure_img": struct_img,
                "smiles":        smiles,
            })

        # AI fallback for ambiguous pages
        if page_ambiguous or (not colored_rects and "Proposal" in page_text):
            try:
                ai_bytes   = page.get_pixmap(matrix=_get_matrix(), alpha=False).tobytes("png")
                ai_records = analyse_page_with_ai(
                    page_image_bytes=ai_bytes,
                    project_id=active_project,
                    theme_id=active_theme,
                    week_date=active_week or "",
                    page_number=page_1base,
                )
                if ai_records:
                    # AI fallback does not return structure images; set to None
                    for r in ai_records:
                        r.setdefault("structure_img", None)
                        r.setdefault("smiles", None)
                    page_records = ai_records
                else:
                    result.flagged_pages.append(page_1base)
                    continue
            except Exception:
                result.flagged_pages.append(page_1base)
                continue

        if colored_rects and not page_records:
            result.flagged_pages.append(page_1base)
            continue

        # ── STEP 6: Deduplicate & accumulate ─────────────────────────────
        seen: set = set()
        for rec in page_records:
            key = (rec["molecule_id"], rec["page_number"])
            if key not in seen:
                seen.add(key)
                result.records.append(rec)
                result.project_ids_seen.add(rec["project_id"])

    doc.close()
    if active_week and not result.week_date:
        result.week_date = active_week
    return result
