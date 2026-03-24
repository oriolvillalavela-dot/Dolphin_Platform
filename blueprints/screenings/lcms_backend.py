from __future__ import annotations

import os
import re
import sys
from typing import Any

import pandas as pd
try:
    from molmass import Formula as MolFormula
except Exception:  # pragma: no cover
    MolFormula = None

try:
    from rdkit import Chem
    from rdkit.Chem import Descriptors, rdMolDescriptors
except Exception:  # pragma: no cover
    Chem = None
    Descriptors = None
    rdMolDescriptors = None


_MODULE_DIR = os.path.dirname(__file__)
_PATH_CANDIDATES = [
    os.path.abspath(os.path.join(_MODULE_DIR, "../../")),   # dolphin_platform/
    os.path.abspath(os.path.join(_MODULE_DIR, "../../../")),  # fallback
    os.path.abspath(os.getcwd()),
]
for _candidate in _PATH_CANDIDATES:
    if _candidate and _candidate not in sys.path:
        sys.path.append(_candidate)

parse_rpt = None
link_peaks_exact = None
proximity_assign = None
auto_assign_roles_per_sample = None
conversion_pct = None
yield_with_is = None
render_presence_map = None
render_heatmap = None
render_pies = None
LCMS_CORE_AVAILABLE = False
LCMS_VISUALS_AVAILABLE = False
LCMS_IMPORT_ERROR = ""
LCMS_VISUALS_IMPORT_ERROR = ""

try:  # pragma: no cover - runtime import availability varies by deployment
    from LCMS_Analysis_Tool.app.parsing.lcms_parser import parse_rpt
    from LCMS_Analysis_Tool.app.analysis import (
        link_peaks_exact,
        proximity_assign,
        auto_assign_roles_per_sample,
        conversion_pct,
        yield_with_is,
    )
    LCMS_CORE_AVAILABLE = True
except Exception as exc:
    LCMS_CORE_AVAILABLE = False
    LCMS_IMPORT_ERROR = str(exc)

try:  # pragma: no cover
    from LCMS_Analysis_Tool.app.visuals import render_presence_map, render_heatmap, render_pies
    LCMS_VISUALS_AVAILABLE = True
except Exception as exc:
    LCMS_VISUALS_AVAILABLE = False
    LCMS_VISUALS_IMPORT_ERROR = str(exc)


def lcms_available() -> bool:
    return bool(LCMS_CORE_AVAILABLE)


def lcms_unavailable_reason() -> str:
    if LCMS_CORE_AVAILABLE:
        return ""
    if LCMS_IMPORT_ERROR:
        return LCMS_IMPORT_ERROR
    return "Unknown LCMS import error."


def _clean_text(value: Any) -> str:
    if value is None:
        return ""
    text = str(value).strip()
    if text.lower() == "nan":
        return ""
    return text


def _safe_float(value: Any):
    text = _clean_text(value)
    if not text:
        return None
    try:
        return float(text)
    except Exception:
        return None


def _formula_from_smiles(smiles: str) -> str:
    if not smiles or Chem is None or rdMolDescriptors is None:
        return ""
    try:
        mol = Chem.MolFromSmiles(smiles)
        if not mol:
            return ""
        return rdMolDescriptors.CalcMolFormula(mol) or ""
    except Exception:
        return ""


def _mw_from_formula(formula: str):
    if not formula:
        return None
    try:
        if MolFormula is not None:
            return float(MolFormula(formula).isotope.mass)
    except Exception:
        pass
    try:
        if MolFormula is not None:
            return float(MolFormula(formula).mass)
    except Exception:
        pass
    return None


def _mw_from_smiles(smiles: str):
    if not smiles or Chem is None or Descriptors is None:
        return None
    try:
        mol = Chem.MolFromSmiles(smiles)
        if not mol:
            return None
        return float(Descriptors.ExactMolWt(mol))
    except Exception:
        return None


def _adducts_for_mw(mw: float):
    mw = float(mw)
    return {
        "[M+H]+": mw + 1.0,
        "[M+2H]2+": mw / 2.0 + 1.0,
        "[M-H]-": mw - 1.0,
        "[M-2H]2-": mw / 2.0 - 1.0,
    }


def _normalize_analysis_type(value: str):
    raw = _clean_text(value).lower()
    if "yield" in raw:
        return "yield_with_is"
    if "conversion" in raw:
        return "conversion"
    if "pie" in raw:
        return "pie_charts"
    return "product_formation"


def _well_from_sample_id(sample_id: str):
    s = _clean_text(sample_id)
    m = re.search(r"_([A-H]\d{1,2})$", s, flags=re.IGNORECASE)
    return m.group(1).upper() if m else ""


def _plate_size_from_dimensions(dimensions: dict):
    rows = int((dimensions or {}).get("rows", 4) or 4)
    cols = int((dimensions or {}).get("columns", 6) or 6)
    return rows * cols


def _target_formula_and_mw(item: dict):
    formula = _clean_text(item.get("formula"))
    smiles = _clean_text(item.get("smiles"))
    mw = _safe_float(item.get("mw"))
    if not formula and smiles:
        formula = _formula_from_smiles(smiles)
    if mw is None and smiles:
        mw = _mw_from_smiles(smiles)
    if mw is None and formula:
        mw = _mw_from_formula(formula)
    # If only formula is provided and no direct MW could be computed from smiles, leave as None.
    return formula, mw


def build_analysis_targets(
    *,
    stmat_entries: list[dict],
    product_entries: list[dict],
    product_overrides: list[dict] | None = None,
    custom_targets: list[dict] | None = None,
):
    targets = []
    sample_role_pairs = []

    stmats = [x for x in (stmat_entries or []) if isinstance(x, dict)]
    products = [x for x in (product_entries or []) if isinstance(x, dict)]
    if not products and product_overrides:
        products = [x for x in product_overrides if isinstance(x, dict)]

    for idx, item in enumerate(stmats, start=1):
        formula, mw = _target_formula_and_mw(item)
        if mw is None:
            continue
        name = _clean_text(item.get("name") or item.get("reactant_name")) or f"SM {idx}"
        role_label = f"SM{idx}"
        adducts = _adducts_for_mw(mw)
        targets.append({
            "name": name,
            "role": "StMat",
            "role_label": role_label,
            "formula": formula or None,
            "smiles": _clean_text(item.get("smiles")) or None,
            "mw": round(float(mw), 4),
            "adducts": {k: round(float(v), 4) for k, v in adducts.items()},
        })
        if formula:
            sample_role_pairs.append((formula, role_label))

    for idx, item in enumerate(products, start=1):
        formula, mw = _target_formula_and_mw(item)
        if mw is None:
            continue
        name = _clean_text(item.get("name") or item.get("product_name")) or f"P {idx}"
        role_label = f"Prod{idx}"
        adducts = _adducts_for_mw(mw)
        targets.append({
            "name": name,
            "role": "Product",
            "role_label": role_label,
            "formula": formula or None,
            "smiles": _clean_text(item.get("smiles")) or None,
            "mw": round(float(mw), 4),
            "adducts": {k: round(float(v), 4) for k, v in adducts.items()},
        })
        if formula:
            sample_role_pairs.append((formula, role_label))

    for idx, item in enumerate((custom_targets or []), start=1):
        if isinstance(item, str):
            item = {"name": item, "formula": item}
        if not isinstance(item, dict):
            continue
        formula, mw = _target_formula_and_mw(item)
        if mw is None:
            continue
        name = _clean_text(item.get("name")) or f"Custom {idx}"
        role_label = f"SideProd{idx}"
        adducts = _adducts_for_mw(mw)
        targets.append({
            "name": name,
            "role": "Custom",
            "role_label": role_label,
            "formula": formula or None,
            "smiles": _clean_text(item.get("smiles")) or None,
            "mw": round(float(mw), 4),
            "adducts": {k: round(float(v), 4) for k, v in adducts.items()},
        })
        if formula:
            sample_role_pairs.append((formula, role_label))

    return {"targets": targets, "sample_role_pairs": sample_role_pairs}


def _rows_cols_for_plate_size(plate_size: int):
    if int(plate_size) == 96:
        return list("ABCDEFGH"), list(range(1, 13))
    if int(plate_size) == 48:
        return list("ABCDEF"), list(range(1, 9))
    return list("ABCD"), list(range(1, 7))


def _render_images(
    *,
    analysis_type: str,
    plate_size: int,
    out_dir: str,
    product_presence: dict,
    conversion_map: dict,
    yield_map: dict,
    pie_map: dict,
):
    if not LCMS_VISUALS_AVAILABLE:
        return []
    os.makedirs(out_dir, exist_ok=True)
    images = []

    if analysis_type == "product_formation":
        fn = "product_presence.png"
        path = os.path.join(out_dir, fn)
        render_presence_map(product_presence, plate_size, "Product Presence", path)
        images.append(path)
    elif analysis_type == "conversion":
        fn = "conversion_heatmap.png"
        path = os.path.join(out_dir, fn)
        render_heatmap(
            conversion_map, plate_size, "Conversion", 0.0, 100.0, "Conversion %",
            path, text_fmt="{:.0f}%", cmap="conversion",
        )
        images.append(path)
    elif analysis_type == "yield_with_is":
        fn = "yield_heatmap.png"
        path = os.path.join(out_dir, fn)
        render_heatmap(
            yield_map, plate_size, "Yield", 0.0, 100.0, "Yield %",
            path, text_fmt="{:.0f}%", cmap="yield",
        )
        images.append(path)
    else:
        fn = "pie_chart.png"
        path = os.path.join(out_dir, fn)
        render_pies(pie_map, plate_size, "Areas by Role", path)
        images.append(path)
    return images


def run_lcms_screening_analysis(
    *,
    rpt_text: str,
    analysis_type: str,
    targets: list[dict],
    yield_params: dict | None,
    dimensions: dict,
    image_dir: str,
):
    if not LCMS_CORE_AVAILABLE:
        raise RuntimeError("LCMS backend modules are not available in this environment.")

    sample_df, peak_df, mass_df = parse_rpt(rpt_text)
    if sample_df is None or sample_df.empty:
        raise RuntimeError("No samples found in RPT file.")

    sample_pairs = []
    for t in targets:
        if not isinstance(t, dict):
            continue
        formula = _clean_text(t.get("formula"))
        role_label = _clean_text(t.get("role_label"))
        if formula and role_label:
            sample_pairs.append((formula, role_label))

    if not sample_pairs:
        raise RuntimeError("No valid LCMS targets could be generated (missing formulas/MW).")

    sample_role_map = {}
    for sid in sample_df["sample_id"].astype(str).tolist():
        sample_role_map[sid] = list(sample_pairs)

    if _normalize_analysis_type(analysis_type) == "yield_with_is":
        y = yield_params or {}
        is_formula = _clean_text(y.get("is_formula"))
        if is_formula:
            for sid in sample_role_map.keys():
                sample_role_map[sid].append((is_formula, "IS"))

    tmerge = link_peaks_exact(peak_df, mass_df)
    tmerge = proximity_assign(tmerge, peak_df, mass_df)
    final_df = auto_assign_roles_per_sample(tmerge, sample_role_map)

    analysis_type_norm = _normalize_analysis_type(analysis_type)
    plate_size = _plate_size_from_dimensions(dimensions)
    rows_lbl, cols_lbl = _rows_cols_for_plate_size(plate_size)
    all_wells = [f"{r}{c}" for r in rows_lbl for c in cols_lbl]

    summary_rows = []
    product_presence = {w: False for w in all_wells}
    conversion_map = {w: 0.0 for w in all_wells}
    yield_map = {w: 0.0 for w in all_wells}
    pie_map = {w: {"SM": 0.0, "Prod": 0.0, "SideProd": 0.0} for w in all_wells}

    by_sample = final_df.groupby("sample_id", dropna=False) if not final_df.empty else []
    for sample_id, sp in by_sample:
        sid = _clean_text(sample_id)
        well = _well_from_sample_id(sid)
        if not well:
            continue
        sm_rows = sp[sp["role"].astype(str).str.startswith("SM", na=False)]
        prod_rows = sp[sp["role"].astype(str).str.startswith("Prod", na=False)]
        side_rows = sp[sp["role"].astype(str).str.startswith("SideProd", na=False)]
        is_rows = sp[sp["role"].astype(str).str.startswith("IS", na=False)]

        sm_area = float(sm_rows["peak_area"].max()) if not sm_rows.empty else 0.0
        prod_area = float(prod_rows["peak_area"].max()) if not prod_rows.empty else 0.0
        side_area = float(side_rows["peak_area"].max()) if not side_rows.empty else 0.0
        is_area = float(is_rows["peak_area"].max()) if not is_rows.empty else 0.0
        conv = float(conversion_pct(sm_area, prod_area)) if (sm_area or prod_area) else float("nan")
        has_prod = bool(prod_area > 0.0)

        yld_pct = float("nan")
        if analysis_type_norm == "yield_with_is":
            yp = yield_params or {}
            prod_target = next((t for t in targets if _clean_text(t.get("role_label")).startswith("Prod")), None)
            mw_prod = (prod_target or {}).get("mw")
            calc = yield_with_is(
                area_prod=prod_area,
                area_is=is_area,
                conc_is_mM=float(_safe_float(yp.get("conc_is_mM")) or 0.0),
                rf=float(_safe_float(yp.get("response_factor")) or 1.0),
                volume_mL=float(_safe_float(yp.get("total_volume_mL")) or 0.0),
                mw_product=float(_safe_float(mw_prod) or 0.0),
                scale_mmol=float(_safe_float(yp.get("reaction_scale_mmol")) or 0.0),
            )
            yld_pct = float(calc.get("yield_pct")) if calc.get("yield_pct") is not None else float("nan")

        result_value = "Yes" if has_prod else "No"
        if analysis_type_norm == "conversion" and conv == conv:  # not nan
            result_value = f"{round(conv, 2)}%"
        if analysis_type_norm == "yield_with_is" and yld_pct == yld_pct:
            result_value = f"{round(yld_pct, 2)}%"

        summary_rows.append({
            "sample_id": sid,
            "well": well,
            "result": result_value,
            "result_type": (
                "Product presence"
                if analysis_type_norm == "product_formation"
                else "LC/MS conversion"
                if analysis_type_norm == "conversion"
                else "LC/MS yield"
                if analysis_type_norm == "yield_with_is"
                else "LC/MS pie chart"
            ),
            "product_found": bool(has_prod),
            "conversion_pct": None if conv != conv else round(conv, 4),
            "yield_pct": None if yld_pct != yld_pct else round(yld_pct, 4),
            "sm_area": round(sm_area, 4),
            "prod_area": round(prod_area, 4),
            "is_area": round(is_area, 4),
        })

        product_presence[well] = bool(has_prod)
        conversion_map[well] = 0.0 if conv != conv else float(conv)
        yield_map[well] = 0.0 if yld_pct != yld_pct else float(yld_pct)
        total = sm_area + prod_area + side_area
        if total > 0:
            pie_map[well] = {
                "SM": sm_area / total,
                "Prod": prod_area / total,
                "SideProd": side_area / total,
            }

    images = _render_images(
        analysis_type=analysis_type_norm,
        plate_size=plate_size,
        out_dir=image_dir,
        product_presence=product_presence,
        conversion_map=conversion_map,
        yield_map=yield_map,
        pie_map=pie_map,
    )

    peaks_cols = [
        "measurement_id", "sample_id", "peak_id", "rt_min", "peak_area", "role",
        "role_source", "found_adduct", "confidence_score",
    ]
    peaks_out = []
    if not final_df.empty:
        final_df = final_df.copy()
        for col in peaks_cols:
            if col not in final_df.columns:
                final_df[col] = None
        peaks_out = final_df[peaks_cols].to_dict(orient="records")

    return {
        "analysis_type": analysis_type_norm,
        "summary_rows": summary_rows,
        "peaks": peaks_out,
        "image_paths": images,
    }
