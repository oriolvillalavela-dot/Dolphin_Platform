import re
from itertools import zip_longest
from typing import List, Dict, Optional, Tuple
import pandas as pd


# -------------------------
# Core text/block utilities
# -------------------------

def _norm(s: str) -> str:
    """Normalize line endings to '\n'."""
    return s.replace("\r\n", "\n").replace("\r", "\n")


def iter_token_blocks(text: str, token: str):
    """
    Yield the INNER text of each [TOKEN] { ... } block from `text`,
    including the FIRST one. Matching is brace-balanced.

    Example matches:
        [SAMPLE]
        {
           ...content...
        }
    """
    t = _norm(text)
    header = re.compile(
        r"^\[\s*" + re.escape(token) + r"\s*\]\s*\n\{",
        re.IGNORECASE | re.MULTILINE,
    )

    pos = 0
    while True:
        m = header.search(t, pos)
        if not m:
            break
        i = m.end()  # position right after the opening '{'
        depth = 1
        j = i
        while j < len(t):
            ch = t[j]
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    # return inner content (without the surrounding braces)
                    yield t[i:j].strip()
                    pos = j + 1
                    break
            j += 1
        else:
            # Unclosed brace; return to EOF
            yield t[i:].strip()
            break


def _first_token_block(text: str, token: str) -> Optional[str]:
    """Return the FIRST [TOKEN] { ... } inner block, or None if not found."""
    for blk in iter_token_blocks(text, token):
        return blk
    return None


def _read_table_after(token: str, block: str) -> List[str]:
    """
    From a SAMPLE/FUNCTION/SPECTRUM block, read the FIRST child table
    like:
        [TOKEN]
        {
           header\tcol2\t...
           row1...
           ...
        }
    Return lines (non-empty). If not present, return [].
    """
    inner = _first_token_block(block, token)
    if inner is None:
        return []
    lines = [ln for ln in _norm(inner).split("\n") if ln.strip()]
    return lines


# -------------------------
# Parsers for sub-blocks
# -------------------------

def parse_sample_header(sample_block: str) -> Dict[str, str]:
    """
    Extract simple key/value lines from the head of SAMPLE block
    (everything before the first [COMPOUND] block, if any).
    """
    s = _norm(sample_block)
    # Cut at the FIRST [COMPOUND] { ... } header (don’t rely on a preceding newline)
    comp_header = re.compile(r"^\[\s*COMPOUND\s*\]\s*\n\{", re.IGNORECASE | re.MULTILINE)
    m = comp_header.search(s)
    head = s[:m.start()] if m else s

    info: Dict[str, str] = {}
    for line in _norm(head).split("\n"):
        if not line.strip() or "\t" not in line:
            continue
        k, *rest = line.split("\t")
        v = rest[0] if rest else ""
        info[k.strip()] = v.strip()
    return info


def parse_ms_spectrum_block(spectrum_block: str):
    """
    Parse a [SPECTRUM] block.
    Prefer 'Peak ID' as the peak identifier; fall back to 'Ref' only if needed.
    """
    out = {"results": [], "ms": [], "time": None, "ref": None, "peak_id": None}
    s = _norm(spectrum_block)

    # Look at early header-like lines for metadata
    early = s.split("\n")[:60]  # a little wider to be safe
    for li in early:
        if "\t" not in li:
            continue
        k, *rest = li.split("\t")
        v = (rest[0] if rest else "").strip()
        kk = k.strip().lower()

        if kk in ("time", "rt", "retentiontime") and out["time"] is None:
            try:
                out["time"] = float(v)
            except Exception:
                pass

        # Prefer Peak ID over Ref
        if kk in ("peak id", "id", "peakid") and out["peak_id"] is None:
            try:
                out["peak_id"] = int(float(v))
            except Exception:
                pass

        # Keep Ref as a fallback identifier
        if (kk == "ref" or "ref" in kk) and out["ref"] is None:
            try:
                out["ref"] = int(float(v))
            except Exception:
                pass

    # RESULTS table (sometimes carries Peak ID/ID as well)
    results_lines = _read_table_after("RESULTS", s)
    if results_lines:
        headers = [h.strip() for h in results_lines[0].split("\t")]
        for row in results_lines[1:]:
            cols = row.split("\t")
            rec = {headers[i]: (cols[i].strip() if i < len(cols) else "") for i in range(len(headers))}
            out["results"].append(rec)

        # If no peak_id yet, try to infer from first results row
        if out["peak_id"] is None and out["results"]:
            rec0 = out["results"][0]
            for key in ("Peak ID", "ID", "PeakID", "Peak Id", "Ref", "Peak Ref", "Reference"):
                if key in rec0 and rec0[key]:
                    try:
                        out["peak_id"] = int(float(rec0[key]))
                        break
                    except Exception:
                        pass

    # MS table
    ms_lines = _read_table_after("MS", s)
    for row in ms_lines:
        if "\t" not in row:
            continue
        m, i = row.split("\t")[0:2]
        try:
            out["ms"].append((float(m), float(i)))
        except Exception:
            pass

    return out


def parse_peak_block(peak_block: str) -> Dict[str, str]:
    """
    Parse a PEAK block into a flat dict of key -> value (strings).
    """
    d: Dict[str, str] = {}
    for line in _norm(peak_block).split("\n"):
        if not line.strip() or "\t" not in line:
            continue
        k, *rest = line.split("\t")
        v = rest[0] if rest else ""
        d[k.strip()] = v.strip()
    return d


def _safe_int(x) -> Optional[int]:
    try:
        if x is None or x == "":
            return None
        return int(float(x))
    except Exception:
        return None


# -------------------------
# Top-level RPT parser
# -------------------------

def parse_rpt(text: str):
    """
    Parse the entire .rpt text into three DataFrames:
      sample_df: Measurement-ID level rows
      peak_df:   PEAK rows (with channel, rt_min, area)
      mass_df:   MS rows (m/z, intensity) + RESULTS formulas where available
    """
    t = _norm(text)

    # Use brace-balanced iterator to include the FIRST [SAMPLE]
    sample_blocks = list(iter_token_blocks(t, "SAMPLE"))

    sample_rows, peak_rows, mass_rows = [], [], []
    run_order = 0

    for sample_idx, sample_block in enumerate(sample_blocks, start=1):
        run_order += 1

        # Each SAMPLE may contain multiple FUNCTION blocks
        function_blocks = list(iter_token_blocks(sample_block, "FUNCTION"))

        # SAMPLE header is everything before first [COMPOUND] block inside SAMPLE
        sample_info = parse_sample_header(sample_block)

        measurement_id = sample_info.get("SampleID") or f"sample_{sample_idx}"
        sample_id_text = sample_info.get("SampleDescription") or ""
        method = sample_info.get("InletMethod") or ""
        date = sample_info.get("Date") or ""
        time = sample_info.get("Time") or ""
        username = sample_info.get("UserName") or ""
        well = sample_info.get("Well") or sample_info.get("Vial") or ""

        # Walk each FUNCTION
        for fb in function_blocks:
            fb_n = _norm(fb)
            head = "\n".join(fb_n.split("\n")[:15]).upper()

            # Channel classification
            if "ES+" in head or "ESI+" in head or "MS POSITIVE" in head:
                desc = "ES+"
            elif "ES-" in head or "ESI-" in head or "MS NEGATIVE" in head:
                desc = "ES-"
            elif "DAD" in head:
                desc = "TAC"
            elif "ANALOG" in head or "ELSD" in head:
                desc = "ELSD"
            else:
                desc = "220 nm"

            # SPECTRUM blocks (MS + RESULTS)
            for sp in iter_token_blocks(fb_n, "SPECTRUM"):
                sp_data = parse_ms_spectrum_block(sp)

                # Resolve peak id once
                sp_peak_id = sp_data.get("peak_id")
                if sp_peak_id is None:
                    sp_peak_id = sp_data.get("ref")

                # MS (m/z, intensity)
                for mass, inten in sp_data["ms"]:
                    mass_rows.append({
                        "measurement_id": measurement_id,
                        "sample_id": sample_id_text,
                        "peak_id": sp_peak_id,        # <-- USE PEAK ID, fallback to Ref
                        "channel": desc,
                        "mass": mass,
                        "intensity": inten,
                    })

                # RESULTS (e.g., Formula table). Keep as formula records (no m/z).
                for res in sp_data["results"]:
                    formula = (res.get("Formula") or res.get("Molecular Formula") or res.get("Compound") or "").strip()
                    found = str(res.get("Found", "1")).strip()
                    if formula and found != "0":
                        mass_rows.append({
                            "measurement_id": measurement_id,
                            "sample_id": sample_id_text,
                            "peak_id": sp_peak_id,    # <-- USE PEAK ID, fallback to Ref
                            "channel": desc,
                            "mass": None,
                            "intensity": None,
                            "formula": formula
                        })

            # PEAK blocks (RT, area, peak_id)
            for pb in iter_token_blocks(fb_n, "PEAK"):
                pk = parse_peak_block(pb)
                try:
                    rt = float(pk.get("Time") or pk.get("RT") or pk.get("RetentionTime") or "nan")
                except Exception:
                    rt = None

                area = None
                for k in ["AreaAbs", "Area", "Area %Total", "Area %BP"]:
                    v = pk.get(k)
                    if v:
                        try:
                            area = float(v)
                            break
                        except Exception:
                            pass

                peak_rows.append({
                    "measurement_id": measurement_id,
                    "sample_id": sample_id_text,
                    "peak_id": _safe_int(pk.get("Peak ID") or pk.get("ID") or pk.get("Ref") or pk.get("Peak Ref")),
                    "rt_min": rt,
                    "peak_area": area,
                    "channel": desc
                })

        # One row per SAMPLE in the sample table
        sample_rows.append({
            "measurement_id": measurement_id,
            "sample_id": sample_id_text,
            "method": method,
            "date": date,
            "time": time,
            "username": username,
            "well": well,
            "run_order": run_order
        })

    return pd.DataFrame(sample_rows), pd.DataFrame(peak_rows), pd.DataFrame(mass_rows)

