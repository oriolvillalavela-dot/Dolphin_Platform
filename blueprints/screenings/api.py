from __future__ import annotations

import io
import itertools
import json
import os
import re
import uuid
import zipfile
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor

import pandas as pd
from flask import jsonify, request, send_file, current_app
from sqlalchemy import func, or_

from database import SessionLocal
from models import Chemical, Screening, ScreeningPlateDesign
from . import screenings_api_bp, plate_designs_api_bp
from .ai_layout import PortkeyLayoutError, generate_layout_with_portkey
from .lcms_backend import (
    lcms_available,
    lcms_unavailable_reason,
    build_analysis_targets,
    run_lcms_screening_analysis,
)
from utils.chem_utils import generate_structure_svg

ALLOWED_STATUSES = {"Planning", "Awaiting Analysis", "Awaiting Validation", "Completed"}
ROLE_ORDER = ["StMat", "Reagent", "Catalyst", "Ligand", "Additive", "Solvent"]
_LCMS_EXECUTOR = ThreadPoolExecutor(max_workers=2)


def _safe_float(value):
    if value is None:
        return None
    text = str(value).strip()
    if not text or text.lower() == "nan":
        return None
    try:
        return float(text)
    except Exception:
        return None


def _clean_text(value):
    if value is None:
        return ""
    text = str(value).strip()
    if text.lower() == "nan":
        return ""
    return text


def _canonical_name(value):
    text = _clean_text(value).lower()
    if not text:
        return ""
    text = re.sub(r"[^a-z0-9]+", "_", text).strip("_")
    return text


def _coerce_datetime(value):
    if value in (None, ""):
        return None
    try:
        dt = pd.to_datetime(value, errors="coerce")
        if pd.isna(dt):
            return None
        return dt.to_pydatetime()
    except Exception:
        return None


def _dimensions_from_size(size_value):
    try:
        size = int(size_value)
    except Exception:
        size = 24
    if size == 96:
        return {"rows": 8, "columns": 12}
    if size == 48:
        return {"rows": 6, "columns": 8}
    return {"rows": 4, "columns": 6}


def _well_ids(rows: int, columns: int):
    labels = []
    for r in range(rows):
        row_label = chr(ord("A") + r)
        for c in range(1, columns + 1):
            labels.append(f"{row_label}{c}")
    return labels


def _row_label(index: int):
    return chr(ord("A") + index)


def _col_label(index: int):
    return str(index + 1)


def _role_from_slot(slot_name: str):
    lowered = (slot_name or "").lower()
    for role in ROLE_ORDER:
        if role.lower() in lowered:
            return role
    return "Reagent"


def _component_key(comp: dict):
    return (
        _clean_text(comp.get("role")).lower(),
        _clean_text(comp.get("name")).lower(),
        _clean_text(comp.get("chem_id")).lower(),
        _clean_text(comp.get("smiles")).lower(),
        _clean_text(comp.get("equivalents")),
        _clean_text(comp.get("fraction")),
    )


def _dedupe_components(components: list[dict]):
    out = []
    seen = set()
    for comp in components:
        if not isinstance(comp, dict):
            continue
        key = _component_key(comp)
        if key in seen:
            continue
        seen.add(key)
        out.append(comp)
    return out


def _normalize_component(raw, role_hint: str | None = None):
    if not isinstance(raw, dict):
        return None
    role = _clean_text(raw.get("role") or role_hint) or "Reagent"

    component = {
        "name": _clean_text(raw.get("name") or raw.get("common_name") or raw.get("label")) or "Unnamed",
        "chem_id": _clean_text(raw.get("chem_id")) or None,
        "role": role,
        "smiles": _clean_text(raw.get("smiles")) or None,
        "cas": _clean_text(raw.get("cas")) or None,
    }

    eq = raw.get("equivalents", raw.get("eq"))
    fr = raw.get("fraction")
    eq_or_fr = raw.get("equivalents_or_fraction")
    if eq in (None, "") and fr in (None, "") and eq_or_fr not in (None, ""):
        if role.lower() == "solvent":
            fr = eq_or_fr
        else:
            eq = eq_or_fr

    eq_val = _safe_float(eq)
    fr_val = _safe_float(fr)
    if eq_val is not None:
        component["equivalents"] = str(eq_val)
    elif eq not in (None, ""):
        component["equivalents"] = _clean_text(eq)
    if fr_val is not None:
        component["fraction"] = str(fr_val)
    elif fr not in (None, ""):
        component["fraction"] = _clean_text(fr)
    return component


def _build_default_axes(dimensions: dict):
    rows = int(dimensions.get("rows", 4))
    cols = int(dimensions.get("columns", 6))
    return {
        "rows": [{"label": _row_label(i), "variables": []} for i in range(rows)],
        "columns": [{"label": _col_label(i), "variables": []} for i in range(cols)],
    }


def _normalize_dimensions(raw_dimensions, fallback_dimensions: dict | None = None):
    dimensions = dict(fallback_dimensions or {"rows": 4, "columns": 6})
    if isinstance(raw_dimensions, dict):
        raw_rows = raw_dimensions.get("rows")
        raw_cols = raw_dimensions.get("columns")
        try:
            rows = int(raw_rows)
            if rows > 0:
                dimensions["rows"] = rows
        except Exception:
            pass
        try:
            cols = int(raw_cols)
            if cols > 0:
                dimensions["columns"] = cols
        except Exception:
            pass
    return dimensions


def _normalize_axes(raw_axes, dimensions: dict):
    rows = int(dimensions.get("rows", 4))
    cols = int(dimensions.get("columns", 6))
    defaults = _build_default_axes(dimensions)

    if not isinstance(raw_axes, dict):
        return defaults

    out_rows = []
    raw_rows = raw_axes.get("rows") if isinstance(raw_axes.get("rows"), list) else []
    for idx in range(rows):
        raw_item = raw_rows[idx] if idx < len(raw_rows) and isinstance(raw_rows[idx], dict) else {}
        vars_raw = raw_item.get("variables") if isinstance(raw_item.get("variables"), list) else []
        vars_norm = []
        for comp in vars_raw:
            item = _normalize_component(comp)
            if item:
                vars_norm.append(item)
        out_rows.append({
            "label": _clean_text(raw_item.get("label")) or _row_label(idx),
            "variables": _dedupe_components(vars_norm),
        })

    out_cols = []
    raw_cols = raw_axes.get("columns") if isinstance(raw_axes.get("columns"), list) else []
    for idx in range(cols):
        raw_item = raw_cols[idx] if idx < len(raw_cols) and isinstance(raw_cols[idx], dict) else {}
        vars_raw = raw_item.get("variables") if isinstance(raw_item.get("variables"), list) else []
        vars_norm = []
        for comp in vars_raw:
            item = _normalize_component(comp)
            if item:
                vars_norm.append(item)
        out_cols.append({
            "label": _clean_text(raw_item.get("label")) or _col_label(idx),
            "variables": _dedupe_components(vars_norm),
        })
    return {"rows": out_rows, "columns": out_cols}


def _build_wells_from_axes(axes: dict, dimensions: dict, existing_wells: dict | None = None):
    rows = int(dimensions.get("rows", 4))
    cols = int(dimensions.get("columns", 6))
    existing_wells = existing_wells if isinstance(existing_wells, dict) else {}

    wells = {}
    for r in range(rows):
        for c in range(cols):
            wid = f"{_row_label(r)}{c+1}"
            row_vars = axes["rows"][r].get("variables") if r < len(axes["rows"]) else []
            col_vars = axes["columns"][c].get("variables") if c < len(axes["columns"]) else []

            existing = existing_wells.get(wid) if isinstance(existing_wells.get(wid), dict) else {}
            existing_unique = existing.get("unique_components")
            existing_components = existing.get("components")
            if isinstance(existing_unique, list):
                unique = [_normalize_component(x) for x in existing_unique]
                unique = [x for x in unique if x]
            elif isinstance(existing_components, list):
                unique = [_normalize_component(x) for x in existing_components]
                unique = [x for x in unique if x]
            else:
                unique = row_vars + col_vars

            if not unique:
                unique = row_vars + col_vars

            wells[wid] = {
                "row_label": _clean_text(existing.get("row_label")) or axes["rows"][r].get("label") or _row_label(r),
                "column_label": _clean_text(existing.get("column_label")) or axes["columns"][c].get("label") or _col_label(c),
                "unique_components": _dedupe_components(unique),
            }
    return wells


def _normalize_design_payload(payload: dict, fallback_dimensions: dict | None = None, fallback_name: str | None = None):
    payload = payload if isinstance(payload, dict) else {}
    dimensions = _normalize_dimensions(payload.get("dimensions"), fallback_dimensions=fallback_dimensions)

    name = (
        _clean_text(payload.get("name"))
        or _clean_text(payload.get("plate_design_name"))
        or _clean_text(payload.get("plate_design"))
        or _clean_text(fallback_name)
        or f"ScreeningDesign_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}"
    )

    global_components = payload.get("global_components") if isinstance(payload.get("global_components"), list) else []
    global_norm = []
    for comp in global_components:
        item = _normalize_component(comp)
        if item:
            global_norm.append(item)
    global_norm = _dedupe_components(global_norm)

    axes = _normalize_axes(payload.get("axes"), dimensions)
    wells = _build_wells_from_axes(axes, dimensions, existing_wells=payload.get("wells"))

    return {
        "name": name,
        "plate_design_name": name,
        "dimensions": dimensions,
        "global_components": global_norm,
        "axes": axes,
        "wells": wells,
    }


def _serialize_design(d: ScreeningPlateDesign):
    normalized = _normalize_design_payload({
        "name": d.name,
        "dimensions": d.dimensions or {},
        "global_components": d.global_components or [],
        "axes": d.axes or {},
        "wells": d.wells or {},
    })
    return {
        "id": d.id,
        "name": normalized["name"],
        "plate_design_name": normalized["name"],
        "dimensions": normalized["dimensions"],
        "global_components": normalized["global_components"],
        "axes": normalized["axes"],
        "wells": normalized["wells"],
        "created_at": d.created_at.isoformat() if d.created_at else None,
        "updated_at": d.updated_at.isoformat() if d.updated_at else None,
    }


def _serialize_screening(s: Screening, include_design: bool = False):
    lcms_state = {}
    if isinstance(s.manual_metadata, dict) and isinstance(s.manual_metadata.get("lcms"), dict):
        lcms_state = s.manual_metadata.get("lcms") or {}
    payload = {
        "eln_id": s.eln_id,
        "project_name": s.project_name,
        "project_id": s.project_id,
        "theme_number": s.theme_number,
        "date": s.date.isoformat() if s.date else None,
        "user": s.user,
        "scale": s.scale,
        "is_photochemistry": bool(s.is_photochemistry),
        "wavelength_nm": s.wavelength_nm,
        "status": s.status,
        "plate_design_id": s.plate_design_id,
        "plate_design_name": s.plate_design.name if s.plate_design else None,
        "manual_metadata": s.manual_metadata or {},
        "eln_stmat_data": s.eln_stmat_data or [],
        "eln_product_data": s.eln_product_data or [],
        "lcms_status": _clean_text(lcms_state.get("status")) or "idle",
        "lcms_final_surf_ready": bool(_clean_text(lcms_state.get("final_surf_path"))),
        "lcms_has_results": bool(isinstance(lcms_state.get("results"), dict) and (lcms_state.get("results") or {}).get("summary_rows")),
        "created_at": s.created_at.isoformat() if s.created_at else None,
        "updated_at": s.updated_at.isoformat() if s.updated_at else None,
    }
    if include_design:
        payload["eln_stmat_data"] = _entries_with_structure(payload.get("eln_stmat_data") or [])
        payload["eln_product_data"] = _entries_with_structure(payload.get("eln_product_data") or [])
        payload["plate_design"] = _serialize_design(s.plate_design) if s.plate_design else None
    return payload


def _entries_with_structure(entries: list[dict]):
    out = []
    cache = {}
    for raw in entries:
        if not isinstance(raw, dict):
            continue
        item = dict(raw)
        smiles = _clean_text(item.get("smiles"))
        if smiles:
            if smiles not in cache:
                try:
                    cache[smiles] = generate_structure_svg(smiles)
                except Exception:
                    cache[smiles] = None
            item["structure_svg"] = cache[smiles]
        else:
            item["structure_svg"] = None
        out.append(item)
    return out


def _screening_storage_dir(eln_id: str):
    safe = re.sub(r"[^A-Za-z0-9_.-]+", "_", _clean_text(eln_id) or "unknown")
    base = os.path.join("data", "screenings_lcms", safe)
    os.makedirs(base, exist_ok=True)
    return base


def _lcms_state(screening: Screening):
    mm = screening.manual_metadata if isinstance(screening.manual_metadata, dict) else {}
    state = mm.get("lcms") if isinstance(mm.get("lcms"), dict) else {}
    return dict(state)


def _set_lcms_state(screening: Screening, state: dict):
    mm = screening.manual_metadata if isinstance(screening.manual_metadata, dict) else {}
    mm = dict(mm)
    mm["lcms"] = state
    screening.manual_metadata = mm


def _normalize_components_by_role(raw_components):
    output = {}
    if not isinstance(raw_components, dict):
        return output

    for slot_name, raw_slot in raw_components.items():
        role = _role_from_slot(slot_name)
        options = []
        if isinstance(raw_slot, dict):
            role = _clean_text(raw_slot.get("role")) or role
            raw_options = raw_slot.get("options") or raw_slot.get("components") or []
            if isinstance(raw_options, list):
                options = raw_options
        elif isinstance(raw_slot, list):
            options = raw_slot

        norm_options = []
        for item in options:
            comp = _normalize_component(item, role_hint=role)
            if comp:
                norm_options.append(comp)
        norm_options = _dedupe_components(norm_options)
        if not norm_options:
            continue
        output[slot_name] = {"role": role, "options": norm_options}
    return output


def _combo_count(slots: list[dict]):
    if not slots:
        return 1
    total = 1
    for slot in slots:
        total *= max(1, len(slot.get("options") or []))
    return total


def _expand_conditions(conditions: list[tuple], target_n: int):
    if not conditions:
        conditions = [tuple()]
    if len(conditions) >= target_n:
        return conditions[:target_n]
    out = []
    idx = 0
    while len(out) < target_n:
        out.append(conditions[idx % len(conditions)])
        idx += 1
    return out


def _flatten_condition_components(cond_tuple):
    comps = []
    for item in cond_tuple:
        if isinstance(item, dict):
            comps.append(item)
    return _dedupe_components(comps)


def _fallback_generate_layout(*, plate_size: int, components_by_role: dict, requested_name: str | None):
    dimensions = _dimensions_from_size(plate_size)
    rows = int(dimensions["rows"])
    cols = int(dimensions["columns"])

    fixed_components = []
    variable_slots = []
    for slot_name, slot_data in components_by_role.items():
        opts = slot_data.get("options") or []
        role = slot_data.get("role") or _role_from_slot(slot_name)
        if len(opts) == 1:
            fixed_components.append(_normalize_component(opts[0], role_hint=role))
        elif len(opts) > 1:
            norm_opts = []
            for option in opts:
                norm = _normalize_component(option, role_hint=role)
                if norm:
                    norm_opts.append(norm)
            variable_slots.append({
                "slot": slot_name,
                "role": role,
                "options": norm_opts,
            })
    fixed_components = _dedupe_components([x for x in fixed_components if x])

    best_row_slots = []
    best_col_slots = []
    if variable_slots:
        best_score = None
        n = len(variable_slots)
        for mask in range(1 << n):
            row_slots = [variable_slots[i] for i in range(n) if mask & (1 << i)]
            col_slots = [variable_slots[i] for i in range(n) if not (mask & (1 << i))]

            row_count = _combo_count(row_slots)
            col_count = _combo_count(col_slots)
            coverage = min(rows, row_count) * min(cols, col_count)
            fit_penalty = abs(rows - row_count) + abs(cols - col_count)
            empty_axis_penalty = (0 if row_slots else 1) + (0 if col_slots else 1)
            score = (coverage, -fit_penalty, -empty_axis_penalty)
            if best_score is None or score > best_score:
                best_score = score
                best_row_slots = row_slots
                best_col_slots = col_slots

    if not best_row_slots and not best_col_slots and variable_slots:
        best_col_slots = [variable_slots[0]]
        if len(variable_slots) > 1:
            best_row_slots = variable_slots[1:]

    row_conditions = [tuple()]
    if best_row_slots:
        row_conditions = list(itertools.product(*[s["options"] for s in best_row_slots]))
    col_conditions = [tuple()]
    if best_col_slots:
        col_conditions = list(itertools.product(*[s["options"] for s in best_col_slots]))

    row_conditions = _expand_conditions(row_conditions, rows)
    col_conditions = _expand_conditions(col_conditions, cols)

    axes = {
        "rows": [],
        "columns": [],
    }
    for r in range(rows):
        axes["rows"].append({
            "label": _row_label(r),
            "variables": _flatten_condition_components(row_conditions[r]),
        })
    for c in range(cols):
        axes["columns"].append({
            "label": _col_label(c),
            "variables": _flatten_condition_components(col_conditions[c]),
        })

    wells = _build_wells_from_axes(axes, dimensions)
    name = _clean_text(requested_name) or f"Fallback_Design_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}"
    return {
        "name": name,
        "plate_design_name": name,
        "dimensions": dimensions,
        "global_components": fixed_components,
        "axes": axes,
        "wells": wells,
    }


def _collect_chem_ids_from_design(normalized_design: dict):
    chem_ids = set()
    for comp in normalized_design.get("global_components") or []:
        cid = _clean_text(comp.get("chem_id"))
        if cid:
            chem_ids.add(cid)
    for _, well_obj in (normalized_design.get("wells") or {}).items():
        if not isinstance(well_obj, dict):
            continue
        for comp in (well_obj.get("unique_components") or []):
            cid = _clean_text(comp.get("chem_id"))
            if cid:
                chem_ids.add(cid)
    return chem_ids


def _fetch_chemical_props_map(session, chem_ids: set[str]):
    if not chem_ids:
        return {}
    lowered = [c.lower() for c in chem_ids]
    rows = (
        session.query(Chemical.chem_id, Chemical.smiles, Chemical.cas)
        .filter(func.lower(Chemical.chem_id).in_(lowered))
        .all()
    )
    mapping = {}
    for chem_id, smiles, cas in rows:
        if not chem_id:
            continue
        mapping[str(chem_id).lower()] = {
            "smiles": smiles,
            "cas": cas,
        }
    return mapping


def _build_rows_for_surf(screening: Screening, design: ScreeningPlateDesign, session, well_results: dict | None = None):
    normalized = _normalize_design_payload({
        "name": design.name,
        "dimensions": design.dimensions,
        "global_components": design.global_components,
        "axes": design.axes,
        "wells": design.wells,
    })
    dimensions = normalized["dimensions"]
    rows = int(dimensions.get("rows", 4))
    columns = int(dimensions.get("columns", 6))
    well_ids = _well_ids(rows, columns)

    global_components = [c for c in normalized["global_components"] if isinstance(c, dict)]
    all_wells = normalized["wells"] or {}
    stmat_data = screening.eln_stmat_data or []
    product_data = screening.eln_product_data or []
    metadata = screening.manual_metadata or {}

    chem_props_map = _fetch_chemical_props_map(session, _collect_chem_ids_from_design(normalized))

    per_well_components = {}
    for wid in well_ids:
        well_obj = all_wells.get(wid) if isinstance(all_wells.get(wid), dict) else {}
        unique_components = []
        if isinstance(well_obj.get("unique_components"), list):
            unique_components = [c for c in well_obj.get("unique_components") if isinstance(c, dict)]
        elif isinstance(well_obj.get("components"), list):
            unique_components = [c for c in well_obj.get("components") if isinstance(c, dict)]

        combined = _dedupe_components(global_components + unique_components)

        with_stmat = []
        for st in stmat_data:
            if not isinstance(st, dict):
                continue
            st_chem_id = _clean_text(st.get("chem_id"))
            st_smiles = _clean_text(st.get("smiles")) or None
            st_cas = _clean_text(st.get("cas")) or None
            if st_chem_id and chem_props_map.get(st_chem_id.lower()):
                props = chem_props_map.get(st_chem_id.lower()) or {}
                st_smiles = props.get("smiles") or st_smiles
                st_cas = props.get("cas") or st_cas
            with_stmat.append({
                "name": _clean_text(st.get("name") or st.get("reactant_name")),
                "smiles": st_smiles,
                "cas": st_cas,
                "role": "StMat",
                "equivalents": _clean_text(st.get("equivalents")) or None,
                "chem_id": st_chem_id or None,
            })
        per_well_components[wid] = _dedupe_components(with_stmat + combined)

    role_max = {role: 0 for role in ROLE_ORDER}
    for comps in per_well_components.values():
        role_counts = {role: 0 for role in ROLE_ORDER}
        for comp in comps:
            role = _clean_text(comp.get("role"))
            role = role if role in ROLE_ORDER else _role_from_slot(role)
            role_counts[role] += 1
        for role in ROLE_ORDER:
            role_max[role] = max(role_max[role], role_counts[role])

    max_products = len([p for p in product_data if isinstance(p, dict)])
    headers = [
        "well",
        "rxn-date",
        "temperature",
        "reaction time",
        "stirring/shaking",
        "wavelength",
    ]
    for role in ROLE_ORDER:
        max_count = role_max[role]
        for idx in range(1, max_count + 1):
            headers.append(f"{role}_{idx}_name")
            headers.append(f"{role}_{idx}_smiles")
            headers.append(f"{role}_{idx}_cas")
            if role == "Solvent":
                headers.append(f"{role}_{idx}_fraction")
            else:
                headers.append(f"{role}_{idx}_eq")
    for idx in range(1, max_products + 1):
        headers.append(f"Product_{idx}_name")
        headers.append(f"Product_{idx}_smiles")
        headers.append(f"Product_{idx}_cas")
    headers.extend(["Result", "Result type"])

    output_rows = []
    rxn_date = screening.date.strftime("%Y-%m-%d") if screening.date else ""
    results_map = {}
    if isinstance(well_results, dict):
        for k, v in well_results.items():
            kk = _clean_text(k).upper()
            if not kk:
                continue
            results_map[kk] = v if isinstance(v, dict) else {"result": v}

    for wid in well_ids:
        row_data = {h: "" for h in headers}
        row_data["well"] = wid
        row_data["rxn-date"] = rxn_date
        row_data["temperature"] = metadata.get("temperature", "")
        row_data["reaction time"] = metadata.get("reaction_time", "")
        row_data["stirring/shaking"] = metadata.get("agitation", "")
        if screening.is_photochemistry:
            row_data["wavelength"] = screening.wavelength_nm if screening.wavelength_nm is not None else metadata.get("wavelength", "")
        else:
            row_data["wavelength"] = ""

        grouped = {role: [] for role in ROLE_ORDER}
        for comp in per_well_components.get(wid, []):
            role = _clean_text(comp.get("role"))
            role = role if role in ROLE_ORDER else _role_from_slot(role)
            grouped[role].append(comp)

        for role in ROLE_ORDER:
            for idx, comp in enumerate(grouped[role], start=1):
                comp_chem_id = _clean_text(comp.get("chem_id"))
                db_props = chem_props_map.get(comp_chem_id.lower()) if comp_chem_id else None
                comp_smiles = (db_props or {}).get("smiles") or comp.get("smiles") or ""
                comp_cas = (db_props or {}).get("cas") or comp.get("cas") or ""
                row_data[f"{role}_{idx}_name"] = comp.get("name") or ""
                row_data[f"{role}_{idx}_smiles"] = comp_smiles
                row_data[f"{role}_{idx}_cas"] = comp_cas
                if role == "Solvent":
                    row_data[f"{role}_{idx}_fraction"] = comp.get("fraction") or ""
                else:
                    row_data[f"{role}_{idx}_eq"] = comp.get("equivalents") or comp.get("eq") or ""

        for idx, prod in enumerate(product_data, start=1):
            prod_chem_id = _clean_text(prod.get("chem_id"))
            prod_db_props = chem_props_map.get(prod_chem_id.lower()) if prod_chem_id else None
            row_data[f"Product_{idx}_name"] = prod.get("name") or prod.get("product_name") or ""
            row_data[f"Product_{idx}_smiles"] = (prod_db_props or {}).get("smiles") or prod.get("smiles") or ""
            row_data[f"Product_{idx}_cas"] = (prod_db_props or {}).get("cas") or prod.get("cas") or ""

        wr = results_map.get(wid)
        if isinstance(wr, dict):
            row_data["Result"] = _clean_text(wr.get("result"))
            row_data["Result type"] = _clean_text(wr.get("result_type"))

        output_rows.append(row_data)
    return pd.DataFrame(output_rows, columns=headers)


def _read_tabular_raw(file_storage):
    filename = _clean_text(file_storage.filename).lower()
    raw_bytes = file_storage.read()
    file_storage.stream.seek(0)
    if not raw_bytes:
        raise ValueError("Uploaded file is empty.")

    def _read_excel_with(engine_name=None):
        stream = io.BytesIO(raw_bytes)
        if engine_name:
            return pd.read_excel(stream, header=None, dtype=str, engine=engine_name)
        return pd.read_excel(stream, header=None, dtype=str)

    if filename.endswith(".csv") or filename.endswith(".txt"):
        return pd.read_csv(io.BytesIO(raw_bytes), header=None, dtype=str)

    if filename.endswith(".xlsx") or filename.endswith(".xlsm") or filename.endswith(".xltx") or filename.endswith(".xltm"):
        return _read_excel_with("openpyxl")

    if filename.endswith(".xls"):
        try:
            return _read_excel_with("xlrd")
        except ImportError as exc:
            raise RuntimeError("XLS support requires xlrd >= 2.0.1. Please install it in the runtime.") from exc

    # Generic fallback for unknown extensions.
    for engine_name in (None, "openpyxl", "xlrd"):
        try:
            return _read_excel_with(engine_name)
        except Exception:
            continue
    raise RuntimeError("Unsupported file format. Upload .csv, .xlsx or .xls.")


def _find_header_row(df_raw: pd.DataFrame, anchor="experiment_name", scan_limit=10):
    max_rows = min(scan_limit, len(df_raw))
    anchor = anchor.lower()
    for idx in range(max_rows):
        vals = [clean.lower() for clean in [_clean_text(v) for v in df_raw.iloc[idx].tolist()]]
        if anchor in vals:
            return idx
    for idx in range(max_rows):
        vals = [clean.lower() for clean in [_clean_text(v) for v in df_raw.iloc[idx].tolist()]]
        if any(anchor in v for v in vals):
            return idx
    raise ValueError('Could not find header anchor "experiment_name" in the first 10 rows.')


def _dataframe_from_dynamic_header(df_raw: pd.DataFrame, header_idx: int):
    raw_headers = [_clean_text(v) for v in df_raw.iloc[header_idx].tolist()]
    dedup_headers = []
    seen = {}
    for idx, head in enumerate(raw_headers):
        base = head or f"unnamed_{idx}"
        key = base.lower()
        seen[key] = seen.get(key, 0) + 1
        if seen[key] > 1:
            dedup_headers.append(f"{base}_{seen[key]}")
        else:
            dedup_headers.append(base)

    df = df_raw.iloc[header_idx + 1:].copy()
    df.columns = dedup_headers
    df = df.dropna(how="all")
    if df.empty:
        raise ValueError("No data rows found below the detected header row.")
    for col in df.columns:
        df[col] = df[col].map(_clean_text)
    return df


def _find_row_with_token(df_raw: pd.DataFrame, token: str, start_idx: int = 0, end_idx: int | None = None):
    token = _canonical_name(token)
    end = len(df_raw) if end_idx is None else min(end_idx, len(df_raw))
    for idx in range(max(0, start_idx), end):
        cells = [_canonical_name(v) for v in df_raw.iloc[idx].tolist()]
        if token in cells:
            return idx
        if any(token and token in c for c in cells):
            return idx
    return None


def _row_is_blank(df_raw: pd.DataFrame, row_idx: int):
    if row_idx < 0 or row_idx >= len(df_raw):
        return True
    for val in df_raw.iloc[row_idx].tolist():
        if _clean_text(val):
            return False
    return True


def _find_first_blank_row(df_raw: pd.DataFrame, start_idx: int = 0, end_idx: int | None = None):
    end = len(df_raw) if end_idx is None else min(end_idx, len(df_raw))
    for idx in range(max(0, start_idx), end):
        if _row_is_blank(df_raw, idx):
            return idx
    return None


def _find_next_non_blank_row(df_raw: pd.DataFrame, start_idx: int = 0, end_idx: int | None = None):
    end = len(df_raw) if end_idx is None else min(end_idx, len(df_raw))
    for idx in range(max(0, start_idx), end):
        if not _row_is_blank(df_raw, idx):
            return idx
    return None


def _headers_dedup(values):
    out = []
    seen = {}
    for idx, head in enumerate(values):
        base = _clean_text(head) or f"unnamed_{idx}"
        key = _canonical_name(base)
        seen[key] = seen.get(key, 0) + 1
        out.append(f"{base}_{seen[key]}" if seen[key] > 1 else base)
    return out


def _block_to_df(df_raw: pd.DataFrame, header_idx: int, end_idx: int | None = None):
    end = len(df_raw) if end_idx is None else max(header_idx + 1, min(end_idx, len(df_raw)))
    headers = _headers_dedup(df_raw.iloc[header_idx].tolist())
    df = df_raw.iloc[header_idx + 1:end].copy()
    df.columns = headers
    df = df.dropna(how="all")
    if df.empty:
        return pd.DataFrame(columns=headers)
    for col in df.columns:
        df[col] = df[col].map(_clean_text)
    return df


def _make_col_resolver(df: pd.DataFrame):
    exact_cols = {str(c).strip().lower(): c for c in df.columns if _clean_text(c)}
    canonical_cols = {_canonical_name(c): c for c in df.columns if _clean_text(c)}

    def _col(*keys):
        for key in keys:
            real = exact_cols.get(key.lower())
            if real is not None:
                return real
        for key in keys:
            real = canonical_cols.get(_canonical_name(key))
            if real is not None:
                return real
        for key in keys:
            token = _canonical_name(key)
            for can_name, real_name in canonical_cols.items():
                if token and token in can_name:
                    return real_name
        return None

    return _col


def _first_non_empty_in_column(df: pd.DataFrame, column_name: str | None):
    if not column_name or df is None or df.empty:
        return ""
    for val in df[column_name].tolist():
        clean = _clean_text(val)
        if clean:
            return clean
    return ""


def _first_non_empty_across_tables(tables_with_resolver, *keys):
    for df, resolver in tables_with_resolver:
        col_name = resolver(*keys)
        val = _first_non_empty_in_column(df, col_name)
        if val:
            return val
    return ""


def _first_non_empty_by_hints(tables_with_resolver, *hint_tokens):
    canonical_hints = [h for h in (_canonical_name(x) for x in hint_tokens) if h]
    if not canonical_hints:
        return ""
    for df, _resolver in tables_with_resolver:
        if df is None or df.empty:
            continue
        for col in df.columns:
            col_can = _canonical_name(col)
            if not col_can:
                continue
            if any((hint in col_can) or (col_can in hint) for hint in canonical_hints):
                val = _first_non_empty_in_column(df, col)
                if val:
                    return val
    return ""


def _select_rows_for_target_experiment(df: pd.DataFrame, experiment_col: str | None, target_eln: str):
    """Filter by experiment while preserving rows where experiment id is omitted after first line."""
    if df is None or df.empty:
        return df
    if not experiment_col or not _clean_text(target_eln):
        return df

    exp_series = df[experiment_col].map(_clean_text).replace("", pd.NA).ffill().fillna("")
    mask = exp_series.map(lambda x: _clean_text(x) == _clean_text(target_eln))
    filtered = df[mask].copy()
    return filtered if not filtered.empty else df


def _dedupe_name_smiles(entries: list[dict]):
    out = []
    seen = set()
    for item in entries:
        if not isinstance(item, dict):
            continue
        key = ((_clean_text(item.get("name"))).lower(), (_clean_text(item.get("smiles"))).lower())
        if key in seen:
            continue
        seen.add(key)
        out.append(item)
    return out


def _row_segments_non_blank(df_raw: pd.DataFrame):
    segments = []
    start = None
    for idx in range(len(df_raw)):
        blank = _row_is_blank(df_raw, idx)
        if not blank and start is None:
            start = idx
        elif blank and start is not None:
            segments.append((start, idx))
            start = None
    if start is not None:
        segments.append((start, len(df_raw)))
    return segments


def _equivalent_is_one(value):
    text = _clean_text(value)
    if not text:
        return False
    compact = text.replace(" ", "")
    if compact in {"1", "1.0", "1.00", "1.000"}:
        return True
    m = re.match(r"^([0-9]+(?:\.[0-9]+)?)", compact)
    if not m:
        return False
    try:
        return abs(float(m.group(1)) - 1.0) < 1e-9
    except Exception:
        return False


def _parse_eln_tables(sm_df: pd.DataFrame, product_df: pd.DataFrame):
    if sm_df.empty and product_df.empty:
        raise ValueError("No parsable data found in ELN export.")

    col_sm = _make_col_resolver(sm_df)
    col_prod = _make_col_resolver(product_df)

    exp_col_sm = col_sm("experiment_name", "experiment_id", "eln_id")
    exp_col_prod = col_prod("experiment_name", "experiment_id", "eln_id")

    target_eln = ""
    if exp_col_sm and not sm_df.empty:
        vals = [v for v in sm_df[exp_col_sm].tolist() if _clean_text(v)]
        if vals:
            target_eln = _clean_text(vals[0])
    if not target_eln and exp_col_prod and not product_df.empty:
        vals = [v for v in product_df[exp_col_prod].tolist() if _clean_text(v)]
        if vals:
            target_eln = _clean_text(vals[0])

    sm_rows = _select_rows_for_target_experiment(sm_df, exp_col_sm, target_eln)
    prod_rows = _select_rows_for_target_experiment(product_df, exp_col_prod, target_eln)

    tables_pref = [
        (sm_rows, col_sm),
        (prod_rows, col_prod),
        (sm_df, col_sm),
        (product_df, col_prod),
    ]

    mmol_col = col_sm("mmol")
    mmol_units_col = col_sm("mmol_units")
    limiting_col = col_sm("limiting")

    reactant_name_col = col_sm("reactant_name", "stmat_name", "starting_material_name")
    reactant_smiles_col = col_sm("reactant_smiles")
    reactant_eq_col = col_sm("equivalents", "reactant_equivalents")
    smiles_col_sm = col_sm("smiles")

    product_name_col = col_prod("product_name")
    product_smiles_col = col_prod("product_smiles")
    smiles_col_prod = col_prod("smiles")

    if not target_eln:
        target_eln = _first_non_empty_across_tables(
            tables_pref,
            "experiment_name", "experiment_id", "eln_id", "eln-id", "eln"
        )
    if not target_eln:
        target_eln = _first_non_empty_by_hints(tables_pref, "experiment", "eln_id", "eln-id", "eln")

    date_raw = _first_non_empty_across_tables(
        tables_pref,
        "date_created_full_char", "date_created", "date", "created_at", "rxn_date", "rxn-date", "run_date"
    )
    if not date_raw:
        date_raw = _first_non_empty_by_hints(tables_pref, "date_created", "date", "rxn_date", "run_date")
    parsed_date = _coerce_datetime(date_raw) if date_raw else None
    user_value = _first_non_empty_across_tables(
        tables_pref, "user_name", "user", "username", "created_by", "chemist", "scientist"
    )
    if not user_value:
        user_value = _first_non_empty_by_hints(tables_pref, "user", "chemist", "scientist", "owner")

    theme_value = _first_non_empty_across_tables(
        tables_pref, "theme_number", "theme_no", "theme nº", "theme n", "theme"
    )
    if not theme_value:
        theme_value = _first_non_empty_by_hints(tables_pref, "theme_number", "theme_no", "theme")

    project_id_value = _first_non_empty_across_tables(
        tables_pref, "project_id", "projectid", "project_code", "project_no"
    )
    if not project_id_value:
        project_id_value = _first_non_empty_by_hints(tables_pref, "project_id", "project_code")

    project_name_value = _first_non_empty_across_tables(
        tables_pref, "project_name", "project", "project_title", "project title"
    )
    if not project_name_value:
        project_name_value = _first_non_empty_by_hints(tables_pref, "project_name", "project")

    # Scale extraction from limiting reagent (fallback first row). Do not stop Block-1 iteration here.
    scale_mmol = ""
    scale_units = ""
    if not sm_rows.empty and mmol_col:
        limit_rows = sm_rows
        if limiting_col:
            limit_rows = sm_rows[sm_rows[limiting_col].map(lambda x: _clean_text(x).upper() == "Y")]
        scale_source = limit_rows.iloc[0] if not limit_rows.empty else sm_rows.iloc[0]
        scale_mmol = _clean_text(scale_source.get(mmol_col))
        scale_units = _clean_text(scale_source.get(mmol_units_col)) if mmol_units_col else ""
    scale = f"{scale_mmol} {scale_units}".strip() if scale_mmol else ""

    stmat_entries = []
    if not sm_rows.empty:
        # Evaluate every row in Block 1 and append all valid reactant_name rows.
        for _, row in sm_rows.iterrows():
            reactant_name = _clean_text(row.get(reactant_name_col)) if reactant_name_col else ""
            if not reactant_name:
                continue
            reactant_smiles = (
                _clean_text(row.get(reactant_smiles_col))
                if reactant_smiles_col else
                (_clean_text(row.get(smiles_col_sm)) if smiles_col_sm else "")
            )
            eq_value = _clean_text(row.get(reactant_eq_col)) if reactant_eq_col else ""
            stmat_entries.append({
                "name": reactant_name,
                "smiles": reactant_smiles or None,
                "equivalents": eq_value or None,
                "role": "StMat",
            })

    product_entries = []
    if not prod_rows.empty:
        for _, row in prod_rows.iterrows():
            product_name = _clean_text(row.get(product_name_col)) if product_name_col else ""
            if not product_name:
                continue
            product_smiles = (
                _clean_text(row.get(product_smiles_col))
                if product_smiles_col else
                (_clean_text(row.get(smiles_col_prod)) if smiles_col_prod else "")
            )
            product_entries.append({
                "name": product_name,
                "smiles": product_smiles or None,
            })

    metadata = {
        "eln_id": target_eln,
        "date": parsed_date.isoformat() if parsed_date else None,
        "scale": scale,
        "scale_value": scale_mmol,
        "scale_units": scale_units,
        "scale_mmol": scale_mmol,
        "user": user_value,
        "theme_number": theme_value,
        "project_id": project_id_value,
        "project_name": project_name_value,
        "project": project_name_value or project_id_value,
    }
    return {
        "metadata": metadata,
        "eln_stmat_data": _dedupe_name_smiles(stmat_entries),
        "eln_product_data": _dedupe_name_smiles(product_entries),
    }


def _parse_eln_raw_dataframe(raw_df: pd.DataFrame):
    # Expected ELN export layout:
    # 1) metadata header row
    # 2) metadata data row
    # 3) blank row
    # 4) SM header row
    # 5) SM rows (1..N)
    # 6) blank row
    # 7) product header row
    # 8) product rows (1..N)
    segments = _row_segments_non_blank(raw_df)
    if len(segments) < 3:
        raise ValueError(
            "Could not detect required 3 data blocks (metadata, starting materials, products) separated by blank rows."
        )

    meta_start, meta_end = segments[0]
    sm_start, sm_end = segments[1]
    prod_start, prod_end = segments[2]

    meta_df = _block_to_df(raw_df, header_idx=meta_start, end_idx=meta_end)
    sm_df = _block_to_df(raw_df, header_idx=sm_start, end_idx=sm_end)
    product_df = _block_to_df(raw_df, header_idx=prod_start, end_idx=prod_end)

    if meta_df.empty:
        raise ValueError("Metadata block found but contains no data row.")
    if sm_df.empty:
        raise ValueError("Starting materials block found but contains no data row.")

    col_meta = _make_col_resolver(meta_df)
    col_sm = _make_col_resolver(sm_df)
    col_prod = _make_col_resolver(product_df)

    eln_id = _first_non_empty_in_column(meta_df, col_meta("experiment_name"))
    user_value = _first_non_empty_in_column(meta_df, col_meta("user_name"))
    date_raw = _first_non_empty_in_column(meta_df, col_meta("date_created_full_char"))
    theme_value = _first_non_empty_in_column(meta_df, col_meta("theme_number"))
    project_id_value = _first_non_empty_in_column(meta_df, col_meta("project_id"))
    project_name_value = _first_non_empty_in_column(meta_df, col_meta("project_name"))
    parsed_date = _coerce_datetime(date_raw) if date_raw else None

    reactant_name_col = col_sm("reactant_name")
    reactant_smiles_col = col_sm("smiles", "reactant_smiles")
    reactant_eq_col = col_sm("equivalents", "reactant_equivalents")
    mmol_col = col_sm("mmol")
    mmol_units_col = col_sm("mmol_units")

    stmat_entries = []
    for _, row in sm_df.iterrows():
        reactant_name = _clean_text(row.get(reactant_name_col)) if reactant_name_col else ""
        if not reactant_name:
            continue
        reactant_smiles = _clean_text(row.get(reactant_smiles_col)) if reactant_smiles_col else ""
        eq_value = _clean_text(row.get(reactant_eq_col)) if reactant_eq_col else ""
        stmat_entries.append({
            "name": reactant_name,
            "smiles": reactant_smiles or None,
            "equivalents": eq_value or None,
            "role": "StMat",
        })

    # Scale = mmol + mmol_units from reactant row with 1 equivalent.
    scale_mmol = ""
    scale_units = ""
    if reactant_name_col and mmol_col:
        for _, row in sm_df.iterrows():
            reactant_name = _clean_text(row.get(reactant_name_col))
            if not reactant_name:
                continue
            eq_value = _clean_text(row.get(reactant_eq_col)) if reactant_eq_col else ""
            if _equivalent_is_one(eq_value):
                scale_mmol = _clean_text(row.get(mmol_col))
                scale_units = _clean_text(row.get(mmol_units_col)) if mmol_units_col else ""
                break
        if not scale_mmol:
            # Fallback to first non-empty reactant row if no eq=1 row is present.
            for _, row in sm_df.iterrows():
                reactant_name = _clean_text(row.get(reactant_name_col))
                if not reactant_name:
                    continue
                scale_mmol = _clean_text(row.get(mmol_col))
                scale_units = _clean_text(row.get(mmol_units_col)) if mmol_units_col else ""
                if scale_mmol:
                    break
    scale = f"{scale_mmol} {scale_units}".strip() if scale_mmol else ""

    product_name_col = col_prod("product_name")
    product_smiles_col = col_prod("smiles", "product_smiles")
    product_entries = []
    if not product_df.empty:
        for _, row in product_df.iterrows():
            product_name = _clean_text(row.get(product_name_col)) if product_name_col else ""
            if not product_name:
                continue
            product_smiles = _clean_text(row.get(product_smiles_col)) if product_smiles_col else ""
            product_entries.append({
                "name": product_name,
                "smiles": product_smiles or None,
            })

    metadata = {
        "eln_id": eln_id,
        "date": parsed_date.isoformat() if parsed_date else None,
        "scale": scale,
        "scale_value": scale_mmol,
        "scale_units": scale_units,
        "scale_mmol": scale_mmol,
        "user": user_value,
        "theme_number": theme_value,
        "project_id": project_id_value,
        "project_name": project_name_value,
        "project": project_name_value or project_id_value,
    }
    return {
        "metadata": metadata,
        "eln_stmat_data": _dedupe_name_smiles(stmat_entries),
        "eln_product_data": _dedupe_name_smiles(product_entries),
    }


@screenings_api_bp.get("")
def list_screenings():
    session = SessionLocal()
    try:
        page = max(1, int(request.args.get("page", 1)))
        page_size = min(100, max(1, int(request.args.get("page_size", 25))))
        status_filter = _clean_text(request.args.get("status"))
        query = _clean_text(request.args.get("query")).lower()

        q = session.query(Screening)
        if status_filter:
            q = q.filter(Screening.status == status_filter)
        if query:
            like = f"%{query}%"
            q = q.filter(or_(
                func.lower(Screening.eln_id).like(like),
                func.lower(Screening.project_name).like(like),
                func.lower(Screening.project_id).like(like),
                func.lower(Screening.theme_number).like(like),
                func.lower(Screening.user).like(like),
            ))
        total = q.count()
        items = (
            q.order_by(Screening.date.desc().nullslast(), Screening.created_at.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
            .all()
        )
        return jsonify({
            "ok": True,
            "items": [_serialize_screening(x) for x in items],
            "page": page,
            "page_size": page_size,
            "total": total,
        })
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500
    finally:
        session.close()


@screenings_api_bp.get("/<string:eln_id>")
def get_screening(eln_id: str):
    session = SessionLocal()
    try:
        screening = session.query(Screening).filter(Screening.eln_id == eln_id).first()
        if not screening:
            return jsonify({"ok": False, "error": "Screening not found"}), 404
        return jsonify({"ok": True, "item": _serialize_screening(screening, include_design=True)})
    finally:
        session.close()


@screenings_api_bp.post("")
def upsert_screening():
    payload = request.get_json(silent=True) or {}
    eln_id = _clean_text(payload.get("eln_id"))
    if not eln_id:
        return jsonify({"ok": False, "error": "eln_id is required"}), 400

    session = SessionLocal()
    try:
        screening = session.query(Screening).filter(Screening.eln_id == eln_id).first()
        if screening is None:
            screening = Screening(eln_id=eln_id)
            session.add(screening)

        status = _clean_text(payload.get("status") or screening.status or "Planning")
        if status not in ALLOWED_STATUSES:
            status = "Planning"

        plate_design_id = payload.get("plate_design_id")
        if plate_design_id:
            design = session.query(ScreeningPlateDesign).filter(ScreeningPlateDesign.id == plate_design_id).first()
            if not design:
                return jsonify({"ok": False, "error": "plate_design_id not found"}), 404
            screening.plate_design_id = design.id

        screening.project_name = payload.get("project_name")
        screening.project_id = payload.get("project_id")
        screening.theme_number = payload.get("theme_number")
        screening.date = _coerce_datetime(payload.get("date"))
        screening.user = payload.get("user")
        screening.scale = payload.get("scale")
        is_photo = payload.get("is_photochemistry")
        manual_metadata = payload.get("manual_metadata") or {}
        if is_photo is None:
            is_photo = bool(manual_metadata.get("photochemistry"))
        screening.is_photochemistry = bool(is_photo)

        wavelength_raw = payload.get("wavelength_nm")
        if wavelength_raw is None:
            wavelength_raw = manual_metadata.get("wavelength")
        screening.wavelength_nm = _safe_float(wavelength_raw) if screening.is_photochemistry else None
        screening.status = status
        if not screening.is_photochemistry:
            manual_metadata["wavelength"] = None
        manual_metadata["photochemistry"] = bool(screening.is_photochemistry)
        screening.manual_metadata = manual_metadata
        screening.eln_stmat_data = payload.get("eln_stmat_data") or []
        screening.eln_product_data = payload.get("eln_product_data") or []

        session.commit()
        session.refresh(screening)
        return jsonify({"ok": True, "item": _serialize_screening(screening)})
    except Exception as exc:
        session.rollback()
        return jsonify({"ok": False, "error": str(exc)}), 500
    finally:
        session.close()


@screenings_api_bp.post("/parse-eln")
def parse_eln():
    if "file" not in request.files:
        return jsonify({"ok": False, "error": "No file uploaded"}), 400
    excel_file = request.files["file"]
    if not excel_file.filename:
        return jsonify({"ok": False, "error": "Missing filename"}), 400

    try:
        raw_df = _read_tabular_raw(excel_file)
        parsed = _parse_eln_raw_dataframe(raw_df)
    except Exception as exc:
        return jsonify({"ok": False, "error": f"Could not parse Excel: {exc}"}), 400
    return jsonify({
        "ok": True,
        "metadata": parsed["metadata"],
        "eln_stmat_data": parsed["eln_stmat_data"],
        "eln_product_data": parsed["eln_product_data"],
    })


@screenings_api_bp.post("/generate-layout")
def generate_layout():
    payload = request.get_json(silent=True) or {}
    plate_size = int(payload.get("plate_size", 24))
    dimensions = _dimensions_from_size(plate_size)
    requested_name = _clean_text(payload.get("plate_design")) or None
    components_by_role = _normalize_components_by_role(payload.get("components_by_role") or {})
    if not components_by_role:
        return jsonify({"ok": False, "error": "components_by_role is required"}), 400

    source = "ai"
    ai_error = None
    try:
        ai_result = generate_layout_with_portkey(
            plate_size=plate_size,
            dimensions=dimensions,
            components_by_role=components_by_role,
            design_name=requested_name,
            max_retries=1,
        )
        design = _normalize_design_payload(
            ai_result,
            fallback_dimensions=dimensions,
            fallback_name=requested_name,
        )
        # Enforce deterministic wells from axis intersections.
        design["wells"] = _build_wells_from_axes(design["axes"], design["dimensions"], existing_wells=None)
    except PortkeyLayoutError as exc:
        source = "fallback"
        ai_error = str(exc)
        design = _fallback_generate_layout(
            plate_size=plate_size,
            components_by_role=components_by_role,
            requested_name=requested_name,
        )
    except Exception as exc:
        source = "fallback"
        ai_error = f"Unexpected AI error: {exc}"
        design = _fallback_generate_layout(
            plate_size=plate_size,
            components_by_role=components_by_role,
            requested_name=requested_name,
        )

    return jsonify({"ok": True, "source": source, "ai_error": ai_error, "design": design})


@screenings_api_bp.post("/<string:eln_id>/generate-surf")
def generate_surf(eln_id: str):
    session = SessionLocal()
    try:
        screening = session.query(Screening).filter(Screening.eln_id == eln_id).first()
        if not screening:
            return jsonify({"ok": False, "error": "Screening not found"}), 404
        if not screening.plate_design_id:
            return jsonify({"ok": False, "error": "No plate design selected for this screening"}), 400

        design = session.query(ScreeningPlateDesign).filter(ScreeningPlateDesign.id == screening.plate_design_id).first()
        if not design:
            return jsonify({"ok": False, "error": "Plate design not found"}), 404

        df = _build_rows_for_surf(screening, design, session)
        output = io.BytesIO()
        with pd.ExcelWriter(output, engine="openpyxl") as writer:
            df.to_excel(writer, index=False, sheet_name="SURF")
        output.seek(0)

        screening.status = "Awaiting Analysis"
        session.commit()

        filename = f"{screening.eln_id}_provisional_surf.xlsx"
        return send_file(
            output,
            mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            as_attachment=True,
            download_name=filename,
        )
    except Exception as exc:
        session.rollback()
        return jsonify({"ok": False, "error": str(exc)}), 500
    finally:
        session.close()


def _run_lcms_analysis_job(app, eln_id: str, job_id: str):
    with app.app_context():
        session = SessionLocal()
        try:
            screening = session.query(Screening).filter(Screening.eln_id == eln_id).first()
            if not screening:
                return
            state = _lcms_state(screening)
            if _clean_text(state.get("job_id")) != _clean_text(job_id):
                return

            rpt_path = _clean_text(state.get("rpt_path"))
            if not rpt_path or not os.path.exists(rpt_path):
                raise RuntimeError("RPT file was not found for analysis job.")

            design = None
            if screening.plate_design_id:
                design = session.query(ScreeningPlateDesign).filter(ScreeningPlateDesign.id == screening.plate_design_id).first()
            dimensions = design.dimensions if design else {"rows": 4, "columns": 6}

            with open(rpt_path, "rb") as fh:
                rpt_text = fh.read().decode("utf-8", errors="ignore")

            targets = state.get("targets") if isinstance(state.get("targets"), list) else []
            analysis_type = _clean_text(state.get("analysis_type")) or "product_formation"
            yield_params = state.get("yield_params") if isinstance(state.get("yield_params"), dict) else {}
            image_dir = _clean_text(state.get("image_dir")) or _screening_storage_dir(eln_id)
            os.makedirs(image_dir, exist_ok=True)

            result = run_lcms_screening_analysis(
                rpt_text=rpt_text,
                analysis_type=analysis_type,
                targets=targets,
                yield_params=yield_params,
                dimensions=dimensions,
                image_dir=image_dir,
            )

            # Ensure peaks is JSON-serialisable (guard against DataFrame if backend changes).
            peaks = result.get("peaks") or []
            if hasattr(peaks, "to_dict"):
                peaks = peaks.to_dict(orient="records")

            images = []
            for path in result.get("image_paths") or []:
                try:
                    rel = os.path.relpath(path, start=app.static_folder).replace(os.sep, "/")
                    url = f"/static/{rel}"
                except ValueError:
                    # Fallback if relpath fails (e.g. different drives on Windows)
                    rel = os.path.relpath(path, start="static").replace(os.sep, "/")
                    url = f"/static/{rel}"
                images.append({
                    "name": os.path.basename(path),
                    "path": path,
                    "url": url,
                })

            state.update({
                "status": "done",
                "completed_at": datetime.utcnow().isoformat(),
                "error": None,
                "results": {
                    "analysis_type": result.get("analysis_type"),
                    "summary_rows": result.get("summary_rows") or [],
                    "peaks": peaks,
                },
                "images": images,
            })
            _set_lcms_state(screening, state)
            screening.status = "Awaiting Validation"
            session.commit()
        except Exception as exc:
            try:
                screening = session.query(Screening).filter(Screening.eln_id == eln_id).first()
                if screening:
                    state = _lcms_state(screening)
                    state.update({
                        "status": "error",
                        "error": str(exc),
                        "completed_at": datetime.utcnow().isoformat(),
                    })
                    _set_lcms_state(screening, state)
                    session.commit()
            except Exception:
                session.rollback()
        finally:
            session.close()


@screenings_api_bp.post("/<string:eln_id>/analysis/targets")
def preview_analysis_targets(eln_id: str):
    session = SessionLocal()
    try:
        screening = session.query(Screening).filter(Screening.eln_id == eln_id).first()
        if not screening:
            return jsonify({"ok": False, "error": "Screening not found"}), 404
        payload = request.get_json(silent=True) or {}
        product_overrides = payload.get("products") if isinstance(payload.get("products"), list) else []
        custom_targets = payload.get("custom_targets") if isinstance(payload.get("custom_targets"), list) else []

        target_info = build_analysis_targets(
            stmat_entries=screening.eln_stmat_data or [],
            product_entries=screening.eln_product_data or [],
            product_overrides=product_overrides,
            custom_targets=custom_targets,
        )
        requires_products = not (screening.eln_product_data or []) and not product_overrides
        return jsonify({
            "ok": True,
            "targets": target_info["targets"],
            "requires_products": requires_products,
            "lcms_available": lcms_available(),
        })
    finally:
        session.close()


@screenings_api_bp.route("/<string:eln_id>/analysis/start", methods=["GET", "POST"])
def start_analysis(eln_id: str):
    session = SessionLocal()
    try:
        screening = session.query(Screening).filter(Screening.eln_id == eln_id).first()
        if not screening:
            return jsonify({"ok": False, "error": "Screening not found"}), 404

        if request.method == "GET":
            state = _lcms_state(screening)
            return jsonify({
                "ok": True,
                "message": "This endpoint starts LC/MS analysis and requires HTTP POST.",
                "how_to_use": {
                    "method": "POST",
                    "multipart_form": {
                        "rpt_file": ".rpt file",
                        "config": {
                            "analysis_type": "product_formation|conversion|yield_with_is|pie_charts",
                            "products": "optional array",
                            "custom_targets": "optional array",
                            "yield_params": "optional object for yield_with_is",
                        },
                    },
                    "json_fallback": {
                        "filename": "input.rpt",
                        "rpt_text": "raw rpt text content",
                        "config": "same as above",
                    },
                },
                "lcms_available": lcms_available(),
                "lcms_unavailable_reason": lcms_unavailable_reason() if not lcms_available() else "",
                "current_status": _clean_text(state.get("status")) or "idle",
                "screening_status": screening.status,
            })

        if not lcms_available():
            reason = lcms_unavailable_reason()
            msg = "LCMS backend is not available on this server."
            if reason:
                msg = f"{msg} Root cause: {reason}"
            return jsonify({"ok": False, "error": msg}), 400
        if not screening.plate_design_id:
            return jsonify({"ok": False, "error": "No plate design selected for this screening"}), 400
        design = session.query(ScreeningPlateDesign).filter(ScreeningPlateDesign.id == screening.plate_design_id).first()
        if not design:
            return jsonify({"ok": False, "error": "Plate design not found"}), 404

        payload = request.get_json(silent=True) or {}
        rpt_file = request.files.get("rpt_file")
        rpt_text_inline = ""
        filename_hint = ""

        cfg = {}
        if rpt_file:
            if not _clean_text(rpt_file.filename):
                return jsonify({"ok": False, "error": "RPT file is required"}), 400
            if not _clean_text(rpt_file.filename).lower().endswith(".rpt"):
                return jsonify({"ok": False, "error": "Only .rpt files are supported"}), 400

            cfg_raw = request.form.get("config")
            if cfg_raw:
                try:
                    cfg = json.loads(cfg_raw)
                except Exception:
                    return jsonify({"ok": False, "error": "Invalid config JSON"}), 400
            filename_hint = _clean_text(rpt_file.filename) or "upload.rpt"
        else:
            cfg = payload.get("config") if isinstance(payload.get("config"), dict) else {}
            rpt_text_inline = payload.get("rpt_text") if isinstance(payload.get("rpt_text"), str) else ""
            filename_hint = _clean_text(payload.get("filename")) or "upload.rpt"
            if not _clean_text(rpt_text_inline):
                return jsonify({"ok": False, "error": "RPT file is required"}), 400
            if not _clean_text(filename_hint).lower().endswith(".rpt"):
                filename_hint = f"{filename_hint}.rpt"

        analysis_type = _clean_text(cfg.get("analysis_type")) or "product_formation"
        yield_params = cfg.get("yield_params") if isinstance(cfg.get("yield_params"), dict) else {}
        product_overrides = cfg.get("products") if isinstance(cfg.get("products"), list) else []
        custom_targets = cfg.get("custom_targets") if isinstance(cfg.get("custom_targets"), list) else []

        if not (screening.eln_product_data or []) and not product_overrides:
            return jsonify({"ok": False, "error": "Product targets are required because ELN product data is empty."}), 400

        target_info = build_analysis_targets(
            stmat_entries=screening.eln_stmat_data or [],
            product_entries=screening.eln_product_data or [],
            product_overrides=product_overrides,
            custom_targets=custom_targets,
        )
        targets = target_info["targets"]
        if not targets:
            return jsonify({"ok": False, "error": "No valid LCMS targets could be generated from the provided data."}), 400

        storage_dir = _screening_storage_dir(eln_id)
        job_id = str(uuid.uuid4())
        rpt_path = os.path.join(storage_dir, f"{job_id}.rpt")
        if rpt_file:
            rpt_file.save(rpt_path)
        else:
            with open(rpt_path, "wb") as fh:
                fh.write(rpt_text_inline.encode("utf-8", errors="ignore"))

        static_dir = os.path.join("static", "screenings", "generated", re.sub(r"[^A-Za-z0-9_.-]+", "_", eln_id), job_id)
        os.makedirs(static_dir, exist_ok=True)

        state = _lcms_state(screening)
        state.update({
            "status": "running",
            "job_id": job_id,
            "filename": filename_hint,
            "analysis_type": analysis_type,
            "yield_params": yield_params,
            "targets": targets,
            "rpt_path": rpt_path,
            "image_dir": static_dir,
            "started_at": datetime.utcnow().isoformat(),
            "error": None,
            "results": None,
            "images": [],
        })
        _set_lcms_state(screening, state)
        screening.status = "Awaiting Analysis"
        session.commit()

        _LCMS_EXECUTOR.submit(_run_lcms_analysis_job, current_app._get_current_object(), eln_id, job_id)

        return jsonify({
            "ok": True,
            "job_id": job_id,
            "status": "running",
            "targets": targets,
        })
    except Exception as exc:
        session.rollback()
        return jsonify({"ok": False, "error": str(exc)}), 500
    finally:
        session.close()


@screenings_api_bp.get("/<string:eln_id>/analysis/status")
def analysis_status(eln_id: str):
    session = SessionLocal()
    try:
        screening = session.query(Screening).filter(Screening.eln_id == eln_id).first()
        if not screening:
            return jsonify({"ok": False, "error": "Screening not found"}), 404
        state = _lcms_state(screening)
        return jsonify({
            "ok": True,
            "status": _clean_text(state.get("status")) or "idle",
            "job_id": state.get("job_id"),
            "error": state.get("error"),
            "screening_status": screening.status,
        })
    finally:
        session.close()


@screenings_api_bp.get("/<string:eln_id>/analysis/results")
def analysis_results(eln_id: str):
    session = SessionLocal()
    try:
        screening = session.query(Screening).filter(Screening.eln_id == eln_id).first()
        if not screening:
            return jsonify({"ok": False, "error": "Screening not found"}), 404
        state = _lcms_state(screening)
        return jsonify({
            "ok": True,
            "status": _clean_text(state.get("status")) or "idle",
            "analysis_type": state.get("analysis_type"),
            "targets": state.get("targets") or [],
            "results": (state.get("results") or {}),
            "images": state.get("images") or [],
            "error": state.get("error"),
            "final_surf_ready": bool(_clean_text(state.get("final_surf_path"))),
        })
    finally:
        session.close()


@screenings_api_bp.post("/<string:eln_id>/analysis/validate")
def finalize_validation(eln_id: str):
    session = SessionLocal()
    try:
        screening = session.query(Screening).filter(Screening.eln_id == eln_id).first()
        if not screening:
            return jsonify({"ok": False, "error": "Screening not found"}), 404
        if not screening.plate_design_id:
            return jsonify({"ok": False, "error": "No plate design selected for this screening"}), 400
        design = session.query(ScreeningPlateDesign).filter(ScreeningPlateDesign.id == screening.plate_design_id).first()
        if not design:
            return jsonify({"ok": False, "error": "Plate design not found"}), 404

        payload = request.get_json(silent=True) or {}
        overrides = payload.get("overrides") if isinstance(payload.get("overrides"), list) else []

        state = _lcms_state(screening)
        results = state.get("results") if isinstance(state.get("results"), dict) else {}
        summary_rows = results.get("summary_rows") if isinstance(results.get("summary_rows"), list) else []
        if not summary_rows:
            return jsonify({"ok": False, "error": "No LCMS analysis results available for validation."}), 400

        override_by_key = {}
        for item in overrides:
            if not isinstance(item, dict):
                continue
            key = _clean_text(item.get("well")).upper() or _clean_text(item.get("sample_id"))
            if not key:
                continue
            override_by_key[key] = item

        merged_rows = []
        well_results = {}
        for row in summary_rows:
            if not isinstance(row, dict):
                continue
            merged = dict(row)
            key = _clean_text(merged.get("well")).upper() or _clean_text(merged.get("sample_id"))
            ov = override_by_key.get(key)
            if isinstance(ov, dict):
                for fld in ("result", "result_type", "conversion_pct", "yield_pct"):
                    if fld in ov:
                        merged[fld] = ov.get(fld)
            merged_rows.append(merged)

            well = _clean_text(merged.get("well")).upper()
            if well:
                well_results[well] = {
                    "result": _clean_text(merged.get("result")),
                    "result_type": _clean_text(merged.get("result_type")),
                }

        df = _build_rows_for_surf(screening, design, session, well_results=well_results)
        out_dir = _screening_storage_dir(eln_id)
        final_path = os.path.join(out_dir, f"{eln_id}_final_surf.xlsx")
        with pd.ExcelWriter(final_path, engine="openpyxl") as writer:
            df.to_excel(writer, index=False, sheet_name="SURF")

        results["summary_rows"] = merged_rows
        state["results"] = results
        state["validated_results"] = merged_rows
        state["status"] = "validated"
        state["final_surf_path"] = final_path
        state["final_surf_filename"] = os.path.basename(final_path)
        state["validated_at"] = datetime.utcnow().isoformat()
        _set_lcms_state(screening, state)
        screening.status = "Completed"
        session.commit()

        return jsonify({
            "ok": True,
            "status": screening.status,
            "final_surf_url": f"/api/screenings/{eln_id}/analysis/final-surf",
            "images_url": f"/api/screenings/{eln_id}/analysis/images",
        })
    except Exception as exc:
        session.rollback()
        return jsonify({"ok": False, "error": str(exc)}), 500
    finally:
        session.close()


@screenings_api_bp.get("/<string:eln_id>/analysis/final-surf")
def download_final_surf(eln_id: str):
    session = SessionLocal()
    try:
        screening = session.query(Screening).filter(Screening.eln_id == eln_id).first()
        if not screening:
            return jsonify({"ok": False, "error": "Screening not found"}), 404
        state = _lcms_state(screening)
        path = _clean_text(state.get("final_surf_path"))
        if not path or not os.path.exists(path):
            return jsonify({"ok": False, "error": "Final SURF file not available."}), 404
        return send_file(
            path,
            mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            as_attachment=True,
            download_name=_clean_text(state.get("final_surf_filename")) or os.path.basename(path),
        )
    finally:
        session.close()


@screenings_api_bp.get("/<string:eln_id>/analysis/images")
def download_analysis_images(eln_id: str):
    session = SessionLocal()
    try:
        screening = session.query(Screening).filter(Screening.eln_id == eln_id).first()
        if not screening:
            return jsonify({"ok": False, "error": "Screening not found"}), 404
        state = _lcms_state(screening)
        images = state.get("images") if isinstance(state.get("images"), list) else []
        existing = [x for x in images if isinstance(x, dict) and _clean_text(x.get("path")) and os.path.exists(_clean_text(x.get("path")))]
        if not existing:
            return jsonify({"ok": False, "error": "No analysis images available."}), 404

        mem = io.BytesIO()
        with zipfile.ZipFile(mem, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            for item in existing:
                p = _clean_text(item.get("path"))
                zf.write(p, arcname=_clean_text(item.get("name")) or os.path.basename(p))
        mem.seek(0)
        return send_file(
            mem,
            mimetype="application/zip",
            as_attachment=True,
            download_name=f"{eln_id}_lcms_images.zip",
        )
    finally:
        session.close()


@screenings_api_bp.post("/<string:eln_id>/analyse")
def mark_awaiting_validation(eln_id: str):
    session = SessionLocal()
    try:
        screening = session.query(Screening).filter(Screening.eln_id == eln_id).first()
        if not screening:
            return jsonify({"ok": False, "error": "Screening not found"}), 404
        screening.status = "Awaiting Validation"
        session.commit()
        return jsonify({"ok": True, "status": screening.status})
    except Exception as exc:
        session.rollback()
        return jsonify({"ok": False, "error": str(exc)}), 500
    finally:
        session.close()


@screenings_api_bp.post("/<string:eln_id>/validate")
def mark_completed(eln_id: str):
    session = SessionLocal()
    try:
        screening = session.query(Screening).filter(Screening.eln_id == eln_id).first()
        if not screening:
            return jsonify({"ok": False, "error": "Screening not found"}), 404
        screening.status = "Completed"
        session.commit()
        return jsonify({"ok": True, "status": screening.status})
    except Exception as exc:
        session.rollback()
        return jsonify({"ok": False, "error": str(exc)}), 500
    finally:
        session.close()


@plate_designs_api_bp.get("")
def list_plate_designs():
    session = SessionLocal()
    try:
        query = _clean_text(request.args.get("query")).lower()
        q = session.query(ScreeningPlateDesign)
        if query:
            q = q.filter(func.lower(ScreeningPlateDesign.name).like(f"%{query}%"))
        items = q.order_by(ScreeningPlateDesign.updated_at.desc().nullslast(), ScreeningPlateDesign.created_at.desc()).all()
        return jsonify({"ok": True, "items": [_serialize_design(x) for x in items]})
    finally:
        session.close()


@plate_designs_api_bp.get("/<string:design_id>")
def get_plate_design(design_id: str):
    session = SessionLocal()
    try:
        design = session.query(ScreeningPlateDesign).filter(ScreeningPlateDesign.id == design_id).first()
        if not design:
            return jsonify({"ok": False, "error": "Design not found"}), 404
        return jsonify({"ok": True, "item": _serialize_design(design)})
    finally:
        session.close()


@plate_designs_api_bp.post("")
def save_plate_design():
    payload = request.get_json(silent=True) or {}
    normalized = _normalize_design_payload(payload)
    name = normalized["name"]
    design_id = payload.get("id")

    session = SessionLocal()
    try:
        if design_id:
            design = session.query(ScreeningPlateDesign).filter(ScreeningPlateDesign.id == design_id).first()
            if not design:
                return jsonify({"ok": False, "error": "Design not found"}), 404
        else:
            design = ScreeningPlateDesign()
            session.add(design)

        duplicate = (
            session.query(ScreeningPlateDesign)
            .filter(func.lower(ScreeningPlateDesign.name) == name.lower(), ScreeningPlateDesign.id != design.id)
            .first()
        )
        if duplicate:
            return jsonify({"ok": False, "error": "A design with this name already exists"}), 409

        design.name = name
        design.dimensions = normalized["dimensions"]
        design.global_components = normalized["global_components"]
        design.axes = normalized["axes"]
        design.wells = normalized["wells"]
        design.updated_at = datetime.utcnow()
        session.commit()
        session.refresh(design)
        return jsonify({"ok": True, "item": _serialize_design(design)})
    except Exception as exc:
        session.rollback()
        return jsonify({"ok": False, "error": str(exc)}), 500
    finally:
        session.close()
