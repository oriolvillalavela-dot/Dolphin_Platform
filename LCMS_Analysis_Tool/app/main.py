import io, os, zipfile, sys, math, re
import pandas as pd
import streamlit as st

# ---------------- Imports ----------------
try:
    from app.parsing.lcms_parser import parse_rpt
    from app.analysis import (
        build_raw_tables, classify_sample_id, link_peaks_exact,
        proximity_assign, auto_assign_roles_per_sample,
        conversion_pct, yield_with_is
    )
    from app.utils import ensure_tmp_session_dir, cleanup_tmp_session_dir, parse_tsv_mapping, mw_from_formula
    from app.visuals import render_presence_map, render_heatmap, render_pies, render_confidence_map
except ModuleNotFoundError:
    sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
    from parsing.lcms_parser import parse_rpt
    from analysis import (
        build_raw_tables, classify_sample_id, link_peaks_exact,
        proximity_assign, auto_assign_roles_per_sample,
        conversion_pct, yield_with_is
    )
    from utils import ensure_tmp_session_dir, cleanup_tmp_session_dir, parse_tsv_mapping, mw_from_formula
    from visuals import render_presence_map, render_heatmap, render_pies, render_confidence_map

# ---------------- Session-state init ----------------
st.set_page_config(page_title="LC/MS Analyser", layout="wide")
st.title("LC/MS Analyser")

def _ss_default(name, val):
    if name not in st.session_state:
        st.session_state[name] = val

_ss_default("analysis_ready", False)
_ss_default("need_recompute", False)             # heavy recompute guard (parse/link/auto-assign)
_ss_default("tmpdir", ensure_tmp_session_dir())
_ss_default("plates", {})                        # per-plate derived data (presence/conv/yield/conf base/meta/well_to_sid)
_ss_default("conf_tables", {})                   # {plate_idx: DataFrame} (preserves Manual check ticks)
_ss_default("last_zip_payload", None)
_ss_default("image_bytes", {})
_ss_default("zip_dirty", False)
_ss_default("raw", None)
_ss_default("sproc_display", None)
_ss_default("mw_table", None)
_ss_default("tmerge", None)                      # main editable peaks table
_ss_default("sample_role_map_cached", {})
_ss_default("current_editor_df", None)           # snapshot of editor for "Update results"
_ss_default("do_update_roles", False)            # latch to apply pending edits

# ---------------- Small helpers ----------------
def show_image(path: str):
    try:
        st.image(path, use_container_width=True)
    except TypeError:
        try:
            st.image(path, use_column_width=True)
        except TypeError:
            st.image(path)

def well_from_sample_id(sample_id: str):
    s = (sample_id or "").strip()
    m = re.search(r"_([A-H]\d{1,2})$", s, flags=re.IGNORECASE)
    return m.group(1).upper() if m else None

def nonempty_series(s):
    try:
        return s.astype(str).str.strip().replace({'nan':'','None':''}).ne("").any()
    except Exception:
        return True

def summarize_masses_list(masses):
    if not isinstance(masses, list) or not masses:
        return (None, None, "")
    mz_int = [(m.get("mass"), m.get("intensity", 0.0)) for m in masses if m.get("mass") is not None]
    if not mz_int:
        return (None, None, "")
    mz_int_sorted = sorted(mz_int, key=lambda x: (x[1] if x[1] is not None else -1), reverse=True)
    top_mz, top_i = mz_int_sorted[0]
    top5 = ", ".join([f"{m:.2f}" for m,_ in mz_int_sorted[:5]])
    return (top_mz, top_i, top5)

def drop_irrelevant_columns(df: pd.DataFrame, always_keep=None) -> pd.DataFrame:
    if always_keep is None: always_keep = []
    def col_empty(s):
        if s.isna().all():
            return True
        try:
            return (s.astype(str).str.strip() == "").all()
        except Exception:
            return False
    keep = []
    for c in df.columns:
        if c in always_keep:
            keep.append(c)
        else:
            if not col_empty(df[c]):
                keep.append(c)
    return df[keep]

def build_mw_table(sample_role_map: dict) -> pd.DataFrame:
    from collections import defaultdict
    roles_seen = defaultdict(list)
    for sid, pairs in sample_role_map.items():
        for f, r in pairs:
            base = (
                "SM" if str(r).startswith("SM")
                else "Prod" if str(r).startswith("Prod")
                else "SideProd" if str(r).startswith("SideProd")
                else "IS" if str(r).startswith("IS")
                else str(r)
            )
            roles_seen[base].append(str(r))
    ordered_cols = []
    for base in ["SM","Prod","SideProd","IS"]:
        uniq = sorted(
            set([x for x in roles_seen.get(base, [])]),
            key=lambda x: (0 if x == base else 1, int(x[len(base):]) if x[len(base):].isdigit() else 0)
        )
        ordered_cols.extend(uniq)
    rows = []
    for sid, pairs in sample_role_map.items():
        rec = {"Sample-ID": sid}
        for formula, role_label in pairs:
            if role_label not in rec:
                mw = mw_from_formula(formula)
                rec[role_label] = round(mw, 2) if mw is not None else None
        rows.append(rec)
    df = pd.DataFrame(rows)
    for c in ordered_cols:
        if c not in df.columns:
            df[c] = None
    return df[["Sample-ID"] + ordered_cols]

def _ensure_manual_check_column(df: pd.DataFrame) -> pd.DataFrame:
    if "manual_check" not in df.columns:
        if "Manual check" in df.columns:
            df = df.rename(columns={"Manual check": "manual_check"})
        else:
            df["manual_check"] = False
    df["manual_check"] = df["manual_check"].fillna(False).astype(bool)
    return df

def _well_labels_for_plate(plate_size: int):
    if plate_size == 96:
        return list("ABCDEFGH"), list(range(1, 13))
    if plate_size == 48:
        return list("ABCDEF"), list(range(1, 9))
    return list("ABCD"), list(range(1, 7))

def _canonicalize_tac_rows(df: pd.DataFrame) -> pd.DataFrame:
    if df is None or df.empty:
        return df
    out = df.copy()
    ch = out.get("channel", pd.Series(index=out.index, dtype=object)).astype(str).str.upper().str.strip()
    rank_map = {"TAC": 0, "DAD": 1, "UV": 2, "220 NM": 3}
    out["__ch_rank"] = ch.map(rank_map).fillna(99).astype(int)
    out["__area_rank"] = -pd.to_numeric(out.get("peak_area"), errors="coerce").fillna(-1e30)
    out["__rt_rank"] = pd.to_numeric(out.get("rt_min"), errors="coerce").fillna(1e30)
    # stable sort → deterministic keep="first"
    out = out.sort_values(
        by=["measurement_id","sample_id","peak_id","__ch_rank","__area_rank","__rt_rank"],
        kind="mergesort"
    )
    out = out.drop_duplicates(["measurement_id","sample_id","peak_id"], keep="first")
    return out.drop(columns=["__ch_rank","__area_rank","__rt_rank"], errors="ignore")

def _ensure_yield_columns(df: pd.DataFrame) -> pd.DataFrame:
    """Make sure the yield table has editable 'yield_pct' and reference 'yield_pct_calc' and 'yield_manual'."""
    if df is None or df.empty:
        return pd.DataFrame(columns=["Sample-ID","Well","conc_product_mM","obtained_mg","expected_mg","yield_pct_calc","yield_pct","yield_manual"])
    out = df.copy()
    if "yield_pct_calc" not in out.columns:
        # If historical version didn't have it, use yield_pct as calc baseline
        out["yield_pct_calc"] = pd.to_numeric(out.get("yield_pct"), errors="coerce")
    out["yield_pct"] = pd.to_numeric(out.get("yield_pct"), errors="coerce")
    out["yield_pct_calc"] = pd.to_numeric(out.get("yield_pct_calc"), errors="coerce")
    if "yield_manual" not in out.columns:
        out["yield_manual"] = False
    out["yield_manual"] = out["yield_manual"].fillna(False).astype(bool)
    return out

def _merge_yield_overrides(new_df: pd.DataFrame, prev_df: pd.DataFrame) -> pd.DataFrame:
    """
    Keep previous manual overrides (yield_manual=True) when rebuilding the yield table.
    If a well was manually overridden before, keep its 'yield_pct' from prev.
    """
    new_df = _ensure_yield_columns(new_df)
    if prev_df is None or prev_df.empty:
        # first build: initialize yield_pct from calc and manual flag False
        new_df["yield_pct"] = new_df["yield_pct_calc"]
        new_df["yield_manual"] = False
        return new_df

    prev_df = _ensure_yield_columns(prev_df)
    key_cols = ["Sample-ID", "Well"]
    merged = new_df.merge(prev_df[key_cols + ["yield_pct","yield_pct_calc","yield_manual"]],
                          on=key_cols, how="left", suffixes=("", "_prev"))

    def _choose(row):
        if bool(row.get("yield_manual_prev", False)):
            return row.get("yield_pct_prev")  # keep user override
        # else default to new calc
        return row.get("yield_pct_calc")

    merged["yield_pct"] = merged.apply(_choose, axis=1)
    merged["yield_manual"] = merged["yield_manual_prev"].fillna(False).astype(bool)
    # Clean up helper cols
    dropc = [c for c in merged.columns if c.endswith("_prev")]
    return merged.drop(columns=dropc, errors="ignore")



# ---------------- Fragments: plate drawings ----------------
@st.fragment
def presence_fragment(plate_idx: int, plate_size: int, png_path: str):
    col1, col2, col3 = st.columns([3,1,1])
    title = col1.text_input(
        f"Title (presence, plate {plate_idx})",
        f"Product presence – Plate {plate_idx}",
        key=f"title_presence_{plate_idx}",
    )
    show_symbols = col2.toggle("Symbols (✓/✗)", value=True, key=f"presence_symbols_{plate_idx}")
    white_symbols = col3.toggle("White symbols", value=True, key=f"presence_white_{plate_idx}")
    symbol_color = "white" if white_symbols else "black"
    fs = col1.number_input(
        f"Axis font size (presence, plate {plate_idx})",
        min_value=6, max_value=20, value=9, step=1, key=f"fs_presence_{plate_idx}"
    )
    data = st.session_state["plates"][plate_idx]["well_ok"]
    render_presence_map(
        well_to_bool=data, nwells=plate_size, title=title, outfile=png_path,
        show_symbols=show_symbols, symbol_color=symbol_color, axis_fontsize=fs,
    )
    try:
        with open(png_path, "rb") as fh:
            st.session_state.image_bytes[os.path.basename(png_path)] = fh.read()
    except Exception:
        pass
    st.session_state.zip_dirty = True
    show_image(png_path)

@st.fragment
def conversion_fragment(plate_idx: int, plate_size: int, png_path: str):
    col1, col2, col3 = st.columns([3,1,1])
    title = col1.text_input(
        f"Title (conversion, plate {plate_idx})",
        f"Conversion – Plate {plate_idx}",
        key=f"title_conv_{plate_idx}",
    )
    show_labels = col2.toggle("Labels", value=True, key=f"conv_labels_{plate_idx}")
    white_labels = col3.toggle("White labels", value=True, key=f"conv_label_color_{plate_idx}")
    label_color = "white" if white_labels else "black"
    fs = col1.number_input(
        f"Axis font size (conversion, plate {plate_idx})",
        min_value=6, max_value=20, value=9, step=1, key=f"fs_conv_{plate_idx}"
    )
    # Fill all wells with a value (0 if missing)
    data = dict(st.session_state["plates"][plate_idx]["conv_pct"])
    rows_lbl, cols_lbl = _well_labels_for_plate(plate_size)
    for r in rows_lbl:
        for c in cols_lbl:
            data.setdefault(f"{r}{c}", 0.0)

    render_heatmap(
        data, plate_size, title, vmin=0.0, vmax=100.0, cbarlabel="Conversion %",
        outfile=png_path, text_fmt="{:.0f}%", cmap="conversion",
        show_labels=show_labels, label_color=label_color, axis_fontsize=fs,
    )
    try:
        with open(png_path, "rb") as fh:
            st.session_state.image_bytes[os.path.basename(png_path)] = fh.read()
    except Exception:
        pass
    st.session_state.zip_dirty = True
    show_image(png_path)

@st.fragment
def yield_fragment(plate_idx: int, plate_size: int, png_path: str):
    col1, col2, col3 = st.columns([3,1,1])
    title = col1.text_input(
        f"Title (yield, plate {plate_idx})",
        f"Yield – Plate {plate_idx}",
        key=f"title_yield_{plate_idx}",
    )
    show_labels = col2.toggle("Labels", value=True, key=f"yield_labels_{plate_idx}")
    white_labels = col3.toggle("White labels", value=True, key=f"yield_label_color_{plate_idx}")
    label_color = "white" if white_labels else "black"
    fs = col1.number_input(
        f"Axis font size (yield, plate {plate_idx})",
        min_value=6, max_value=20, value=9, step=1, key=f"fs_yield_{plate_idx}"
    )
    # Fill all wells with a value (0 if missing)
    data = dict(st.session_state["plates"][plate_idx]["yield_pct"])
    rows_lbl, cols_lbl = _well_labels_for_plate(plate_size)
    for r in rows_lbl:
        for c in cols_lbl:
            data.setdefault(f"{r}{c}", 0.0)

    render_heatmap(
        data, plate_size, title, vmin=0.0, vmax=100.0, cbarlabel="Yield %",
        outfile=png_path, text_fmt="{:.0f}%", cmap="yield",
        show_labels=show_labels, label_color=label_color, axis_fontsize=fs,
    )
    try:
        with open(png_path, "rb") as fh:
            st.session_state.image_bytes[os.path.basename(png_path)] = fh.read()
    except Exception:
        pass
    st.session_state.zip_dirty = True
    show_image(png_path)

@st.fragment
def pies_fragment(plate_idx: int, plate_size: int, png_path: str):
    col1, col2 = st.columns([3, 1])
    title = col1.text_input(
        f"Title (pie chart, plate {plate_idx})",
        f"Areas by role – Plate {plate_idx}",
        key=f"title_pies_{plate_idx}",
    )
    axis_fs = col2.number_input(
        f"Axis font size (pies, plate {plate_idx})",
        min_value=6, max_value=20, value=10, step=1, key=f"fs_pies_{plate_idx}"
    )
    data = st.session_state["plates"][plate_idx]["fracs"]
    render_pies(data, plate_size, title, png_path, label_color="black", axis_fontsize=axis_fs)
    try:
        with open(png_path, "rb") as fh:
            st.session_state.image_bytes[os.path.basename(png_path)] = fh.read()
    except Exception:
        pass
    st.session_state.zip_dirty = True
    show_image(png_path)

# --- Confidence IMAGE fragment (used in popover); reads conf_tables[plate_idx] ---
@st.fragment
def confidence_image_fragment(plate_idx: int, plate_size: int, png_path: str):
    # Build map from current conf table; if absent, use base meta
    rows_lbl, cols_lbl = _well_labels_for_plate(plate_size)
    grid_map = {f"{r}{c}": 0.0 for r in rows_lbl for c in cols_lbl}

    dfc = st.session_state.conf_tables.get(plate_idx)
    if isinstance(dfc, pd.DataFrame) and not dfc.empty and "Well" in dfc and "final_confidence" in dfc:
        for _, rr in dfc.iterrows():
            w = str(rr["Well"]).strip().upper()
            try:
                grid_map[w] = float(rr["final_confidence"])
            except Exception:
                pass
    else:
        # Fallback to base confidence (no table yet)
        base = st.session_state.plates.get(plate_idx, {}).get("confidence_base", {}) or {}
        for w, v in base.items():
            try:
                grid_map[w] = float(v)
            except Exception:
                pass

    render_confidence_map(
        grid_map, plate_size, f"Role assignment confidence – Plate {plate_idx}",
        png_path, axis_fontsize=9, label_color="white", show_labels=True,
    )
    try:
        with open(png_path, "rb") as fh:
            st.session_state.image_bytes[os.path.basename(png_path)] = fh.read()
    except Exception:
        pass
    st.session_state.zip_dirty = True
    show_image(png_path)

# ---------------- Builders: derived data, confidence table, and updater ----------------
def _summarize_masses(masses):
    if not isinstance(masses, list) or not masses:
        return (None, None, None)
    vals = [(d.get("mass"), d.get("intensity", 0.0)) for d in masses if d.get("mass") is not None]
    if not vals: return (None, None, None)
    vals.sort(key=lambda t: (t[1] if t[1] is not None else -1), reverse=True)
    top_mz, top_i = vals[0]
    top5 = ", ".join(f"{m:.2f}" for m,_ in vals[:5])
    return (top_mz, top_i, top5)

def build_plates_from_tmerge(plate_size: int, sample_role_map_cached: dict):
    """
    Recompute only light, derived plate metrics from st.session_state.tmerge and raw sample order.
    Preserves manual yield overrides.
    """
    raw_samples = (st.session_state.raw or {}).get("Raw_Sample_Data", pd.DataFrame()).copy()
    if raw_samples.empty:
        st.session_state.plates = {}
        return

    # Keep a reference to previous plates to preserve manual yields
    prev_plates = st.session_state.plates or {}

    # Detect plates by well repetition order
    sproc2 = raw_samples.copy()
    class_cols2 = sproc2["Sample-ID"].apply(classify_sample_id).apply(pd.Series)
    sproc2 = pd.concat([sproc2, class_cols2], axis=1)
    if "well_from_id" not in sproc2.columns:
        sproc2["well_from_id"] = sproc2["Sample-ID"].apply(well_from_sample_id)
    sproc2["Well"] = sproc2["well_from_id"].fillna("").astype(str).str.strip().str.upper()

    def detect_plate_groups_by_well(sproc_df: pd.DataFrame, plate_size: int):
        rows = sproc_df.sort_values("RunOrder").reset_index(drop=True)
        groups, cur, seen = [], [], set()
        for _, r in rows.iterrows():
            w = str(r.get("Well","")).strip().upper()
            if not w:
                continue
            if (w in seen) or (len(seen) >= plate_size):
                if cur: groups.append(cur)
                cur, seen = [], set()
            cur.append((r["Measurement-ID"], r["Sample-ID"], w))
            seen.add(w)
        if cur: groups.append(cur)
        return groups

    groups = detect_plate_groups_by_well(sproc2, plate_size)
    st.session_state.plates = {}
    if not groups:
        return

    updated = st.session_state.tmerge.copy()

    for plate_idx, group in enumerate(groups, start=1):
        wells = {sid: w for (_, sid, w) in group}
        sub_peaks = updated[updated["Sample-ID"].isin(wells.keys())].copy()

        # --- Product presence
        well_ok = {}
        for sid, well in wells.items():
            sp = sub_peaks[sub_peaks["Sample-ID"] == sid]
            well_ok[well] = bool(sp["role"].astype(str).str.lower().str.startswith("prod").any())

        # --- Conversion
        wells_to_conv, rows_conv = {}, []
        for sid, well in wells.items():
            sp = sub_peaks[sub_peaks["Sample-ID"] == sid]
            roles_base = sp["role"].astype(str).str.lower().str.extract(r"^([a-z]+)", expand=False).fillna("")
            a_prod = sp.loc[roles_base.eq("prod"), "peak_area"].max() if roles_base.eq("prod").any() else None
            a_sm   = sp.loc[roles_base.eq("sm"),   "peak_area"].max() if roles_base.eq("sm").any()   else None
            if (a_prod is None) and (a_sm is not None): conv = 0.0
            elif (a_sm is None) and (a_prod is not None): conv = 100.0
            else: conv = conversion_pct(a_sm, a_prod)
            wells_to_conv[well] = conv
            rows_conv.append({"Sample-ID": sid, "Well": well, "conv_pct": conv, "area_SM": a_sm, "area_Prod": a_prod})

        # --- Yield (compute calc values; manual overrides merged later)
        rows_yield_calc = []
        prod_mw_per_sid = {}
        for sid2, pairs in sample_role_map_cached.items():
            for f, r in pairs:
                if str(r).lower().startswith("prod"):
                    prod_mw_per_sid[sid2] = mw_from_formula(f); break
        fallback_mw = next((mw_from_formula(f) for _, pairs in sample_role_map_cached.items() for f, r in pairs if str(r).lower().startswith("prod")), None)

        # global params
        rf      = st.session_state.get("rf", None)
        conc_is = st.session_state.get("conc_is", None)
        volume  = st.session_state.get("volume", None)
        scale   = st.session_state.get("scale", None)

        for sid, well in wells.items():
            sp = sub_peaks[sub_peaks["Sample-ID"] == sid]
            roles_base = sp["role"].astype(str).str.lower().str.extract(r"^([a-z]+)", expand=False).fillna("")
            a_prod = sp.loc[roles_base.eq("prod"), "peak_area"].max() if roles_base.eq("prod").any() else None
            a_is   = sp.loc[roles_base.eq("is"),   "peak_area"].max() if roles_base.eq("is").any()   else None
            mw = prod_mw_per_sid.get(sid, fallback_mw)

            if a_prod is None or (isinstance(a_prod, (int,float)) and a_prod <= 0):
                calc = 0.0
                rows_yield_calc.append({
                    "Sample-ID": sid, "Well": well,
                    "conc_product_mM": 0.0, "obtained_mg": 0.0,
                    "expected_mg": (mw * scale) if (mw is not None and scale is not None) else float("nan"),
                    "yield_pct_calc": calc
                })
            elif (mw is not None and a_is is not None and rf not in (None, 0) and conc_is is not None and volume is not None and scale is not None):
                res = yield_with_is(a_prod, a_is, conc_is, rf, volume, mw, scale)
                calc = res.get("yield_pct", float("nan"))
                rows_yield_calc.append({
                    "Sample-ID": sid, "Well": well,
                    "conc_product_mM": res.get("conc_product_mM", float("nan")),
                    "obtained_mg":     res.get("obtained_mg", float("nan")),
                    "expected_mg":     res.get("expected_mg", float("nan")),
                    "yield_pct_calc":  calc
                })
            else:
                rows_yield_calc.append({
                    "Sample-ID": sid, "Well": well,
                    "conc_product_mM": float("nan"),
                    "obtained_mg": float("nan"),
                    "expected_mg": float("nan"),
                    "yield_pct_calc": float("nan")
                })

        # Build new yield table, then merge previous manual overrides
        df_yield_new = pd.DataFrame(rows_yield_calc)
        df_yield_new = _ensure_yield_columns(df_yield_new)

        prev_yield = None
        if plate_idx in prev_plates:
            prev_tables = (prev_plates[plate_idx] or {}).get("tables", {})
            prev_yield = prev_tables.get("yield")
        df_yield_final = _merge_yield_overrides(df_yield_new, prev_yield)

        # Final yield map for heatmap = editable yield_pct
        wells_to_yield = {}
        for _, r in df_yield_final.iterrows():
            wells_to_yield[str(r["Well"]).strip().upper()] = float(r["yield_pct"]) if pd.notna(r["yield_pct"]) else float("nan")

        # --- Pie fractions
        wells_to_fracs, rows_pies = {}, []
        for sid, well in wells.items():
            sp = sub_peaks[sub_peaks["Sample-ID"] == sid]
            role_l = sp["role"].astype(str).str.lower()
            a_sm   = sp.loc[role_l.str.startswith("sm"),   "peak_area"].max() if role_l.str.startswith("sm").any()   else 0.0
            a_prod = sp.loc[role_l.str.startswith("prod"), "peak_area"].max() if role_l.str.startswith("prod").any() else 0.0
            a_side = sp.loc[role_l.str.startswith("side"), "peak_area"].max() if role_l.str.startswith("side").any() else 0.0
            tot = (a_sm or 0.0) + (a_prod or 0.0) + (a_side or 0.0)
            fr = {"NA": 1.0} if not tot or math.isnan(tot) else {
                "SM": (a_sm or 0.0)/tot, "Prod": (a_prod or 0.0)/tot, "SideProd": (a_side or 0.0)/tot
            }
            wells_to_fracs[well] = fr
            rows_pies.append({"Sample-ID": sid, "Well": well, "SM": a_sm, "Prod": a_prod, "SideProd": a_side})

        # --- Confidence base + meta (from current tmerge)
        wells_to_conf_base, wells_to_conf_meta = {}, {}
        for sid, well in wells.items():
            sp = sub_peaks[sub_peaks["Sample-ID"] == sid].copy()
            conf_val = 0.0
            method = ""
            if not sp.empty and "confidence_score" in sp.columns:
                sc = pd.to_numeric(sp["confidence_score"], errors="coerce")
                sp["__sc"] = sc
                prod_mask = sp["role"].astype(str).str.lower().str.startswith("prod")
                sp_prod = sp[prod_mask & sp["__sc"].notna()]
                if not sp_prod.empty:
                    best_idx = sp_prod["__sc"].idxmax()
                    conf_val = float(sp_prod.at[best_idx, "__sc"])
                    rs = str(sp_prod.at[best_idx, "role_source"]) if "role_source" in sp_prod.columns else ""
                    rk = sp_prod.at[best_idx, "match_rank"] if "match_rank" in sp_prod.columns else None
                    ri = sp_prod.at[best_idx, "rel_intensity"] if "rel_intensity" in sp_prod.columns else None
                    if rs == "rpt_confirmed": method = "Prod via rpt_confirmed"
                    elif rs == "mz_top_k":    method = f"Prod via m/z match (rank={rk}, relI={ri:.2f})" if rk is not None and ri is not None else "Prod via m/z match"
                    elif rs == "manual_input": method = "Prod via manual input"
                    else:                     method = "Prod (unknown source)"
                else:
                    sp_assigned = sp[sp["__sc"].notna()]
                    if not sp_assigned.empty:
                        best_idx = sp_assigned["__sc"].idxmax()
                        conf_val = float(sp_assigned.at[best_idx, "__sc"])
                        rs = str(sp_assigned.at[best_idx, "role_source"]) if "role_source" in sp_assigned.columns else ""
                        rk = sp_assigned.at[best_idx, "match_rank"] if "match_rank" in sp_assigned.columns else None
                        ri = sp_assigned.at[best_idx, "rel_intensity"] if "rel_intensity" in sp_assigned.columns else None
                        if rs == "rpt_confirmed": method = "Best role via rpt_confirmed"
                        elif rs == "mz_top_k":    method = f"Best role via m/z match (rank={rk}, relI={ri:.2f})" if rk is not None and ri is not None else "Best role via m/z match"
                        elif rs == "manual_input": method = "Best role via manual input"
                        else:                     method = "Best role (unknown source)"
            wells_to_conf_base[well] = float(max(0.0, min(100.0, conf_val)))
            wells_to_conf_meta[well] = method

        st.session_state.plates[plate_idx] = {
            "well_ok": well_ok,
            "conv_pct": wells_to_conv,
            "yield_pct": wells_to_yield,  # <- heatmap reads this (final yield - overrides applied)
            "fracs": wells_to_fracs,
            "confidence_base": wells_to_conf_base,
            "confidence_meta": wells_to_conf_meta,
            "well_to_sid": {w: s for s, w in wells.items()},
            "tables": {
                "conversion": pd.DataFrame(rows_conv),
                "yield": _ensure_yield_columns(df_yield_final),  # includes calc, editable, manual flag
                "pies": pd.DataFrame(rows_pies),
            },
        }


def build_conf_table_for_plate(plate_idx: int):
    """
    Build/refresh the per-plate confidence table (preserves Manual check).
    Final confidence = geometric mean of available per-role confidences, unless Manual check=on → 100.
    """
    plate = st.session_state.plates.get(plate_idx, {})
    well_to_sid = plate.get("well_to_sid", {})
    base = plate.get("confidence_base", {}) or {}
    meta = plate.get("confidence_meta", {}) or {}

    # collect per-role confidences from tmerge
    rows = []
    for well, sid in sorted(well_to_sid.items()):
        df = st.session_state.tmerge
        sp = df[df["Sample-ID"] == sid].copy()
        def role_best_conf(prefix):
            if sp.empty or "confidence_score" not in sp.columns:
                return float("nan")
            mask = sp["role"].astype(str).str.lower().str.startswith(prefix)
            sub = sp[mask]
            if sub.empty: return float("nan")
            sc = pd.to_numeric(sub["confidence_score"], errors="coerce")
            mx = sc.max(skipna=True)
            return float(mx) if pd.notna(mx) else float("nan")
        rows.append({
            "Well": well,
            "SM_conf": role_best_conf("sm"),
            "Prod_conf": role_best_conf("prod"),
            "SideProd_conf": role_best_conf("side"),
            "IS_conf": role_best_conf("is"),
            "base_confidence": float(base.get(well, 0.0)),
            "calc_method": meta.get(well, ""),
        })
    fresh = pd.DataFrame(rows)

    # merge prior ticks
    prior = st.session_state.conf_tables.get(plate_idx)
    if isinstance(prior, pd.DataFrame) and not prior.empty:
        prior = _ensure_manual_check_column(prior)
        if {"Well","manual_check"}.issubset(prior.columns):
            fresh = fresh.merge(prior[["Well","manual_check"]], on="Well", how="left")
    fresh = _ensure_manual_check_column(fresh)

    # final confidence calculation (geometric mean)
    role_cols = ["SM_conf", "Prod_conf", "SideProd_conf", "IS_conf"]
    def geom_mean(vals):
        vals = [float(v) for v in vals if pd.notna(v)]
        if not vals:
            return float("nan")
        if any(v <= 0.0 for v in vals):
            return 0.0
        logs = [math.log(v/100.0) for v in vals]
        return float(100.0 * math.exp(sum(logs)/len(logs)))

    def compute_final(row):
        if bool(row.get("manual_check")):
            return 100.0
        vals = [row[c] for c in role_cols if pd.notna(row[c])]
        gm = geom_mean(vals) if vals else float("nan")
        return float(gm) if pd.notna(gm) else float(row["base_confidence"])

    fresh["final_confidence"] = fresh.apply(compute_final, axis=1)
    st.session_state.conf_tables[plate_idx] = fresh

def apply_manual_role_changes(edited_df: pd.DataFrame):
    """
    Compare edited_df vs st.session_state.tmerge; apply changes with role_source='manual_input', confidence_score=100.
    """
    updated = st.session_state.tmerge.copy()
    key_cols = ["measurement_id","Sample-ID","peak_id"]
    for col in ["role_source","confidence_score","found_adduct"]:
        if col not in updated.columns:
            updated[col] = None if col != "confidence_score" else float("nan")

    # normalize roles
    old = updated[key_cols + ["role"]].copy()
    old["role"] = old["role"].astype(str).fillna("").str.strip()
    new = edited_df[key_cols + ["role"]].copy()
    new["role"] = new["role"].astype(str).fillna("").str.strip()

    merged = old.merge(new, on=key_cols, how="left", suffixes=("_old","_new"))
    changed = merged[merged["role_old"] != merged["role_new"]]

    for _, r in changed.iterrows():
        cond = (
            (updated["measurement_id"]==r["measurement_id"]) &
            (updated["Sample-ID"]==r["Sample-ID"]) &
            (updated["peak_id"]==r["peak_id"])
        )
        if r["role_new"] != "":
            updated.loc[cond, "role"] = r["role_new"]
            updated.loc[cond, "role_source"] = "manual_input"
            updated.loc[cond, "confidence_score"] = 100.0
            updated.loc[cond, "found_adduct"] = "manual_input"
        else:
            # cleared role: keep role_source as manual_input, confidence to 0
            updated.loc[cond, "role"] = ""
            updated.loc[cond, "role_source"] = "manual_input"
            updated.loc[cond, "confidence_score"] = 0.0
            updated.loc[cond & (updated["found_adduct"]=="manual_input"), "found_adduct"] = None

    # helper cols (no resort to keep scroll position)
    updated["role_l"] = updated["role"].astype(str).str.lower().fillna("")
    updated["role_base"] = updated["role_l"].str.extract(r"^([a-z]+)", expand=False).fillna("")
    st.session_state.tmerge = updated
    st.session_state.zip_dirty = True

def update_results_after_manual_changes(plate_size: int):
    """
    Light recompute after applying manual role changes:
    - rebuild plates derived data
    - rebuild confidence tables (preserving manual ticks)
    """
    sample_role_map_cached = st.session_state.sample_role_map_cached or {}
    build_plates_from_tmerge(plate_size, sample_role_map_cached)
    # rebuild confidence tables per plate (preserve ticks)
    for plate_idx in (st.session_state.plates or {}).keys():
        build_conf_table_for_plate(plate_idx)

# ---------------- UI ----------------
uploaded_rpt = st.file_uploader("RPT file", type=["rpt"])
mapping_file = st.file_uploader("Mapping file (TSV/CSV): position, sample_id, chem...", type=["tsv","csv","txt"])

st.markdown("---")
colA, colB = st.columns(2)
scope = colA.radio("Experiment scope", ["Single reaction", "Plate"], index=0, horizontal=True)
atype = colB.selectbox("Analysis type", ["Product formation", "Conversion", "Yield with IS", "Pie charts (plate only)"])
plate_size = st.selectbox("Plate size (for plate mode)", [24, 48, 96], index=0) if scope == "Plate" else None

st.session_state.analysis_type = atype
st.session_state.scope = scope
st.session_state.plate_size = plate_size
if uploaded_rpt is not None and getattr(uploaded_rpt, "name", None):
    _base = os.path.splitext(uploaded_rpt.name)[0]
    import re as _re
    _base = _re.sub(r"[^\w\-.]+", "_", _base)
    st.session_state.zip_basename = _base
else:
    st.session_state.zip_basename = "results"

# ---------------- Mapping roles (per-column) ----------------
mapping_df = None
sample_role_map = {}
role_order_seen = []

if mapping_file is not None:
    mapping_df = parse_tsv_mapping(mapping_file.getvalue())
    st.subheader("Mapping file preview")
    st.dataframe(mapping_df, use_container_width=True, hide_index=True)
    all_cols = list(mapping_df.columns)
    chem_cols_all = [c for c in all_cols[2:] if nonempty_series(mapping_df[c])]
    st.markdown("**Assign roles for each chem column (applies to that column per row):**")
    role_options = ["(ignore)", "SM", "Prod", "SideProd", "IS"]
    cols = st.columns(len(chem_cols_all) if chem_cols_all else 1)
    if not chem_cols_all:
        cols[0].markdown("_No chem columns detected in mapping file._")
        col_roles = []
    else:
        col_roles = [cols[i].selectbox(f"{c} role", role_options, index=0, key=f"role_{c}") for i,c in enumerate(chem_cols_all)]
    for _, r in mapping_df.iterrows():
        sid = str(r.get("sample_id","")).strip()
        if not sid:
            continue
        role_counts, pairs = {}, []
        for c, base_role in zip(chem_cols_all, col_roles):
            if base_role == "(ignore)":
                continue
            f = str(r.get(c,"")).strip()
            if not f:
                continue
            role_counts[base_role] = role_counts.get(base_role, 0) + 1
            role_label = base_role if role_counts[base_role] == 1 else f"{base_role}{role_counts[base_role]}"
            pairs.append((f, role_label))
            if role_label not in role_order_seen:
                role_order_seen.append(role_label)
        if pairs:
            sample_role_map.setdefault(sid, []).extend(pairs)

st.markdown("### Additional chemical formulas and roles (optional, global)")
st.caption("These formulas will be added to every Sample-ID detected in the RPT (un-numbered).")
chem_df = pd.DataFrame({"formula": ["", "", ""], "role": ["", "", ""]})
chem_df = st.data_editor(chem_df, num_rows="dynamic", use_container_width=True, key="chemforms")

# Store yield params into session (so light recompute can read them)
if atype == "Yield with IS":
    col1, col2, col3, col4 = st.columns(4)
    st.session_state.rf      = col1.number_input("Response factor (RF)", min_value=0.0, value=float(st.session_state.get("rf", 1.0) or 1.0), step=0.1, format="%.4f")
    st.session_state.conc_is = col2.number_input("Concentration IS (mM)", min_value=0.0, value=float(st.session_state.get("conc_is", 10.0) or 10.0), step=0.1, format="%.3f")
    st.session_state.volume  = col3.number_input("Total volume (mL)",     min_value=0.0, value=float(st.session_state.get("volume", 1.0) or 1.0), step=0.1, format="%.3f")
    st.session_state.scale   = col4.number_input("Scale (mmol)",          min_value=0.0, value=float(st.session_state.get("scale", 0.100000) or 0.100000), step=0.000001, format="%.6f")

st.markdown("---")
if st.button("Run analysis", type="primary", disabled=uploaded_rpt is None):
    st.session_state.analysis_ready = True
    st.session_state.need_recompute = True
    # keep conf_tables for now; rebuilt when plates computed

# ---------------- Main analysis ----------------
if st.session_state.analysis_ready and uploaded_rpt is not None:
    tmpdir = st.session_state.tmpdir or ensure_tmp_session_dir()
    st.session_state.tmpdir = tmpdir

    # ---------- HEAVY RECOMPUTE only when requested ----------
    if st.session_state.need_recompute:
        text = uploaded_rpt.getvalue().decode("utf-8", errors="ignore")
        sample_df, peak_df, mass_df = parse_rpt(text)

        # Add global manual pairs to each sample
        manual_pairs = []
        if isinstance(chem_df, pd.DataFrame) and "formula" in chem_df and "role" in chem_df:
            for _, row in chem_df.iterrows():
                f = str(row["formula"]).strip()
                r = str(row["role"]).strip()
                if f and r:
                    manual_pairs.append((f, r))
        if manual_pairs:
            for sid in sample_df["sample_id"].astype(str).unique():
                sample_role_map.setdefault(sid, []).extend(manual_pairs)

        st.session_state.sample_role_map_cached = dict(sample_role_map)

        raw = build_raw_tables(sample_df, peak_df, mass_df)
        st.session_state.raw = raw

        # Classification
        sproc = raw["Raw_Sample_Data"].copy()
        class_cols = sproc["Sample-ID"].apply(classify_sample_id).apply(pd.Series)
        sproc = pd.concat([sproc, class_cols], axis=1)
        sproc_display = drop_irrelevant_columns(
            sproc,
            always_keep=["Measurement-ID","Sample-ID","Well","class","eln_id","RunOrder","Date","Time","Username","Method"]
        )
        st.session_state.sproc_display = sproc_display.copy()

        # MW table
        st.session_state.mw_table = build_mw_table(sample_role_map) if sample_role_map else None

        # ---------- Linking + roles (Option B: preserve masses for ALL TAC peaks) ----------
        # 1) Link masses by exact key and (if needed) proximity
        tac_linked = link_peaks_exact(peak_df, mass_df)
        tac_linked = proximity_assign(tac_linked, peak_df, mass_df, rt_tol=0.05)
        tac_linked = _canonicalize_tac_rows(tac_linked)

        # Save per-TAC masses BEFORE any de-duplication (normalize key dtypes)
        masses_pre = tac_linked[["measurement_id","sample_id","peak_id","masses"]].copy()
        masses_pre["measurement_id"] = masses_pre["measurement_id"].astype(str).str.strip()
        masses_pre["sample_id"]      = masses_pre["sample_id"].astype(str).str.strip()
        masses_pre["peak_id"]        = pd.to_numeric(masses_pre["peak_id"], errors="coerce")

        # 2) Assign roles WITH de-duplication (keeps one “best” TAC per role),
        #    while we still keep the full TAC list for the UI table.
        tac_assigned = auto_assign_roles_per_sample(
            tac_linked, sample_role_map, mz_tol_da=0.5, dedupe_per_role=True
        )
        tac_assigned = tac_assigned.copy()
        tac_assigned["measurement_id"] = tac_assigned["measurement_id"].astype(str).str.strip()
        tac_assigned["sample_id"]      = tac_assigned["sample_id"].astype(str).str.strip()
        tac_assigned["peak_id"]        = pd.to_numeric(tac_assigned["peak_id"], errors="coerce")

        # 3) Build TAC-only editor table (ALL TAC rows)
        tac_only = peak_df.copy()
        tac_only["__ch"] = tac_only["channel"].astype(str).str.upper().str.strip()
        tac_only = tac_only[tac_only["__ch"].isin(["TAC", "DAD", "UV", "220 NM"])].drop(columns="__ch")
        tac_only = tac_only[["measurement_id","sample_id","peak_id","rt_min","peak_area","channel"]].copy()
        tac_only["measurement_id"] = tac_only["measurement_id"].astype(str).str.strip()
        tac_only["sample_id"]      = tac_only["sample_id"].astype(str).str.strip()
        tac_only["peak_id"]        = pd.to_numeric(tac_only["peak_id"], errors="coerce")
        tac_only["Sample-ID"]      = tac_only["sample_id"]
        tac_only = _canonicalize_tac_rows(tac_only)

        keys = ["measurement_id","sample_id","peak_id"]

        # 4) Left-join masses for ALL TAC peaks, then left-join a single (possibly deduped) role row if present
        roles_cols = ["role","role_source","found_adduct","match_rank","rel_intensity","confidence_score"]
        avail_cols = [c for c in roles_cols if c in tac_assigned.columns]

        tmerge_auto = (
            tac_only
            .merge(masses_pre, on=keys, how="left")
            .merge(
                tac_assigned[keys + avail_cols].drop_duplicates(keys, keep="first"),
                on=keys, how="left"
            )
        )

        # Ensure 'masses' is always a list
        tmerge_auto["masses"] = tmerge_auto["masses"].apply(lambda x: x if isinstance(x, list) else [])

        # Summaries and helper columns
        tmerge_auto[["top_mz","top_intensity","top5_mz"]] = tmerge_auto["masses"].apply(lambda m: pd.Series(_summarize_masses(m)))
        tmerge_auto["role_l"] = tmerge_auto["role"].astype(str).str.lower().fillna("")
        tmerge_auto["role_base"] = tmerge_auto["role_l"].str.extract(r"^([a-z]+)", expand=False).fillna("")
        tmerge_auto["peak_id_num"] = pd.to_numeric(tmerge_auto["peak_id"], errors="coerce")
        tmerge_auto = tmerge_auto.sort_values(by=["measurement_id","peak_id_num","rt_min"], na_position="last", kind="mergesort").drop(columns=["peak_id_num"])

        st.session_state.tmerge = tmerge_auto.copy()

        # Initial plates + confidence tables
        build_plates_from_tmerge(plate_size, st.session_state.sample_role_map_cached)
        for plate_idx in (st.session_state.plates or {}).keys():
            build_conf_table_for_plate(plate_idx)

        st.session_state.need_recompute = False

    # ---------- From here on: LIGHT updates only ----------
    st.subheader("Raw Data")
    raw = st.session_state.raw or {"Raw_Sample_Data": pd.DataFrame(), "Raw_Peak_Data": pd.DataFrame(), "Raw_Mass_Data": pd.DataFrame()}
    st.caption(f"Parsed: {len(raw['Raw_Sample_Data'])} samples, {len(raw['Raw_Peak_Data'])} peaks, {len(raw['Raw_Mass_Data'])} MS rows")
    st.dataframe(raw["Raw_Sample_Data"], use_container_width=True, hide_index=True)
    st.dataframe(raw["Raw_Peak_Data"], use_container_width=True, hide_index=True)
    st.dataframe(raw["Raw_Mass_Data"], use_container_width=True, hide_index=True)
    c1, c2, c3 = st.columns(3)
    c1.download_button("Download Raw_Sample_Data.csv", raw["Raw_Sample_Data"].to_csv(index=False).encode("utf-8"), "Raw_Sample_Data.csv")
    c2.download_button("Download Raw_Peak_Data.csv", raw["Raw_Peak_Data"].to_csv(index=False).encode("utf-8"), "Raw_Peak_Data.csv")
    c3.download_button("Download Raw_Mass_Data.csv", raw["Raw_Mass_Data"].to_csv(index=False).encode("utf-8"), "Raw_Mass_Data.csv")
    st.session_state.zip_dirty = True

    st.subheader("Sample-ID classification")
    sproc_display = st.session_state.sproc_display
    if sproc_display is not None and not sproc_display.empty:
        st.dataframe(sproc_display, use_container_width=True, hide_index=True)

    st.subheader("Molecular weights per Sample-ID")
    if st.session_state.mw_table is not None and not st.session_state.mw_table.empty:
        st.dataframe(st.session_state.mw_table, use_container_width=True, hide_index=True)
        st.download_button("Download_MW_Table.csv", st.session_state.mw_table.to_csv(index=False).encode("utf-8"), "PerSample_MW_Table.csv")
    else:
        st.info("No roles/formulas provided in mapping or manual table.")

    # ---------- UV peaks with linked ESI masses and roles (EDITOR) ----------
    st.subheader("UV peaks with linked ESI masses and roles")
    tmerge_current = st.session_state.tmerge.copy() if st.session_state.tmerge is not None else pd.DataFrame()

    role_choices = sorted(
        {r for r in tmerge_current.get("role", pd.Series(dtype=str)).dropna().astype(str).str.strip().unique()}
        | {"","sm","prod","sideprod","is","sm1","sm2","prod1","prod2"}
    )

    cols_for_editor = [
        "measurement_id","Sample-ID","peak_id","rt_min","peak_area",
        "top_mz","top_intensity","top5_mz","found_adduct","role","role_source","match_rank","rel_intensity","confidence_score"
    ]
    present_cols = [c for c in cols_for_editor if c in tmerge_current.columns]

    edited_roles_df = st.data_editor(
        tmerge_current[present_cols],
        use_container_width=True,
        hide_index=True,
        disabled=[c for c in present_cols if c not in ("role",)],  # only role editable
        column_config={
            "role": st.column_config.SelectboxColumn(
                "role",
                options=role_choices,
                help="Edit the assigned role for this TAC peak. After finishing edits, click 'Update results' below.",
                required=False,
            )
        },
        key="tac_peaks_roles_editor",
    )
    # Stash the current editor snapshot so the Update button can consume it
    st.session_state.current_editor_df = edited_roles_df.copy()

    # Update button (applies all role changes at once)
    if st.button("Update results", type="primary", help="Apply manual role edits and refresh derived outputs"):
        st.session_state.do_update_roles = True

    if st.session_state.do_update_roles:
        st.session_state.do_update_roles = False
        if isinstance(st.session_state.current_editor_df, pd.DataFrame):
            apply_manual_role_changes(st.session_state.current_editor_df)
            update_results_after_manual_changes(plate_size)

    # ---------------- Analysis outputs (LIGHT recompute from edited roles) ----------------
    st.subheader("Analysis outputs")
    sample_role_map_cached = st.session_state.sample_role_map_cached or {}

    if scope == "Single reaction":
        if st.session_state.raw:
            meas_ids = sorted(st.session_state.raw["Raw_Sample_Data"]["Measurement-ID"].unique().tolist())
        else:
            meas_ids = []
        meas_sel = st.selectbox("Select Measurement-ID", meas_ids, index=0) if meas_ids else None
        sub = st.session_state.tmerge[st.session_state.tmerge["measurement_id"]==meas_sel] if meas_sel else pd.DataFrame()
        roles_base = sub["role"].astype(str).str.lower().str.extract(r"^([a-z]+)", expand=False).fillna("")
        if atype == "Product formation":
            st.success(f"Product formed: {'Yes' if not sub.empty and roles_base.eq('prod').any() else 'No'}")
        elif atype == "Conversion":
            area_prod = sub.loc[roles_base.eq("prod"), "peak_area"].max() if not sub.empty and roles_base.eq("prod").any() else None
            area_sm   = sub.loc[roles_base.eq("sm"),   "peak_area"].max() if not sub.empty and roles_base.eq("sm").any()   else None
            if (area_prod is None) and (area_sm is not None): conv = 0.0
            elif (area_sm is None) and (area_prod is not None): conv = 100.0
            else: conv = conversion_pct(area_sm, area_prod)
            st.write({"area_SM": area_sm, "area_Prod": area_prod, "conversion_pct": conv})
        elif atype == "Yield with IS":
            area_prod = sub.loc[roles_base.eq("prod"),"peak_area"].max() if not sub.empty and roles_base.eq("prod").any() else None
            area_is   = sub.loc[roles_base.eq("is"),  "peak_area"].max() if not sub.empty and roles_base.eq("is").any()   else None
            prod_formula = None
            for sid, pairs in sample_role_map_cached.items():
                for f, r in pairs:
                    if str(r).lower().startswith("prod"):
                        prod_formula = f; break
                if prod_formula: break
            mw = mw_from_formula(prod_formula or "")
            rf      = st.session_state.get("rf", None)
            conc_is = st.session_state.get("conc_is", None)
            volume  = st.session_state.get("volume", None)
            scale   = st.session_state.get("scale", None)
            res = yield_with_is(area_prod, area_is, conc_is, rf, volume, mw, scale) if (mw is not None and area_prod is not None and area_is is not None) else {}
            st.write({"area_Prod": area_prod, "area_IS": area_is, "MW": mw, **res})
        else:
            st.info("Pie charts are for Plate mode.")
    else:
        # Ensure plates are built (after possible update)
        if not st.session_state.plates:
            build_plates_from_tmerge(plate_size, sample_role_map_cached)
            for plate_idx in (st.session_state.plates or {}).keys():
                build_conf_table_for_plate(plate_idx)

        # ---- Render per plate ----
        for plate_idx, pdata in st.session_state.plates.items():
            png_presence = os.path.join(tmpdir, f"plate{plate_idx}_presence.png")
            png_conv     = os.path.join(tmpdir, f"plate{plate_idx}_conversion.png")
            png_yield    = os.path.join(tmpdir, f"plate{plate_idx}_yield.png")
            png_pies     = os.path.join(tmpdir, f"plate{plate_idx}_pies.png")
            conf_png     = os.path.join(tmpdir, f"plate{plate_idx}_confidence.png")

            hcol1, hcol2 = st.columns([6, 1])
            with hcol1:
                st.markdown(f"#### Plate {plate_idx}")
            with hcol2:
                # Confidence IMAGE remains in a popover
                with st.popover("Result confidence"):
                    confidence_image_fragment(plate_idx, plate_size, conf_png)

            # Confidence TABLE inline (outside popover)
            st.markdown("**Confidence table**")
            build_conf_table_for_plate(plate_idx)  # refresh but preserves Manual check
            dfc = st.session_state.conf_tables.get(plate_idx, pd.DataFrame()).copy()
            # show with Manual check editable; recompute final on the fly
            ui = dfc.rename(columns={"manual_check": "Manual check"})
            edited_conf = st.data_editor(
                ui,
                use_container_width=True,
                hide_index=True,
                disabled=[c for c in ui.columns if c != "Manual check"],
                key=f"conf_editor_{plate_idx}",
            )
            # sync ticks and recompute final conf; preserve in session
            back = edited_conf.rename(columns={"Manual check": "manual_check"})
            back = _ensure_manual_check_column(back)

            # recompute final with same rule
            role_cols = ["SM_conf", "Prod_conf", "SideProd_conf", "IS_conf"]
            def geom_mean(vals):
                vals = [float(v) for v in vals if pd.notna(v)]
                if not vals: return float("nan")
                if any(v <= 0 for v in vals): return 0.0
                logs = [math.log(v/100.0) for v in vals]
                return float(100.0 * math.exp(sum(logs)/len(logs)))
            def compute_final(row):
                if bool(row.get("manual_check")): return 100.0
                vals = [row[c] for c in role_cols if pd.notna(row[c])]
                gm = geom_mean(vals) if vals else float("nan")
                return float(gm) if pd.notna(gm) else float(row.get("base_confidence", 0.0))
            back["final_confidence"] = back.apply(compute_final, axis=1)
            st.session_state.conf_tables[plate_idx] = back

            # Now the usual plate visual(s)
            if atype == "Product formation":
                presence_fragment(plate_idx, plate_size, png_presence)
            elif atype == "Conversion":
                conversion_fragment(plate_idx, plate_size, png_conv)
                st.dataframe(pdata["tables"]["conversion"], use_container_width=True, hide_index=True)
            elif atype == "Yield with IS":
                # Editable yield table first -> update state -> then render image from state
                dfy = st.session_state.plates[plate_idx]["tables"]["yield"].copy()
                dfy = _ensure_yield_columns(dfy)

                st.markdown("**Yield table (editable %)**")
                # Show both calc and editable; only 'yield_pct' is editable by the user
                ui = dfy.rename(columns={
                    "yield_pct_calc": "Yield % (calc)",
                    "yield_pct": "Yield % (final/editable)",
                    "yield_manual": "Manual override"
                })

                edited_yield = st.data_editor(
                    ui,
                    use_container_width=True,
                    hide_index=True,
                    disabled=[c for c in ui.columns if c not in ("Yield % (final/editable)",)],
                    key=f"yield_editor_{plate_idx}",
                )

                # Sync back to session, flag manual rows and refresh the map dict
                back = edited_yield.rename(columns={
                    "Yield % (calc)": "yield_pct_calc",
                    "Yield % (final/editable)": "yield_pct",
                    "Manual override": "yield_manual",
                })
                back = _ensure_yield_columns(back)

                # manual flag: true if user-edited differs from calc (tiny epsilon tolerance)
                eps = 1e-9
                back["yield_manual"] = (back["yield_pct"] - back["yield_pct_calc"]).abs() > eps

                # Save table back to state
                st.session_state.plates[plate_idx]["tables"]["yield"] = back

                # Update the mapping used by the heatmap (reads plates[...]["yield_pct"])
                new_map = {}
                for _, r in back.iterrows():
                    new_map[str(r["Well"]).strip().upper()] = float(r["yield_pct"]) if pd.notna(r["yield_pct"]) else float("nan")
                st.session_state.plates[plate_idx]["yield_pct"] = new_map

                # Now render the image from the fresh state
                yield_fragment(plate_idx, plate_size, png_yield)

                # Optional: show the updated table below the image
                if not back.empty:
                    st.dataframe(back, use_container_width=True, hide_index=True)
            else:
                pies_fragment(plate_idx, plate_size, png_pies)
                st.dataframe(pdata["tables"]["pies"], use_container_width=True, hide_index=True)

# ------------- Bundle downloadable artifacts -------------
def _df_to_csv_bytes(df: pd.DataFrame) -> bytes:
    return df.to_csv(index=False).encode("utf-8")

def build_results_zip_from_state() -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        raw = st.session_state.get("raw")
        if raw:
            zf.writestr("Raw Data/sample_data.csv", raw["Raw_Sample_Data"].to_csv(index=False).encode("utf-8"))
            zf.writestr("Raw Data/peak_data.csv",   raw["Raw_Peak_Data"].to_csv(index=False).encode("utf-8"))
            zf.writestr("Raw Data/mass_data.csv",   raw["Raw_Mass_Data"].to_csv(index=False).encode("utf-8"))

        sproc_display = st.session_state.get("sproc_display")
        if sproc_display is not None and not sproc_display.empty:
            zf.writestr("Processed Data/Sample_ID_Classification.csv", sproc_display.to_csv(index=False).encode("utf-8"))

        mw_table = st.session_state.get("mw_table")
        if mw_table is not None and not mw_table.empty:
            zf.writestr("Processed Data/PerSample_MW_Table.csv", mw_table.to_csv(index=False).encode("utf-8"))

        tmerge = st.session_state.get("tmerge")
        if tmerge is not None and not tmerge.empty:
            cols_pref = ["measurement_id","Sample-ID","peak_id","rt_min","peak_area","top_mz","top_intensity","top5_mz","found_adduct","role"]
            cols = [c for c in cols_pref if c in tmerge.columns]
            zf.writestr("Processed Data/TAC_Peaks_Linked_Masses_Roles.csv", tmerge[cols].to_csv(index=False).encode("utf-8"))

        # per-plate processed tables (including Confidence)
        plates = st.session_state.get("plates") or {}
        conf_tables = st.session_state.get("conf_tables") or {}
        for pidx, pdata in plates.items():
            tables = pdata.get("tables", {})
            df = tables.get("conversion")
            if df is not None and not df.empty:
                zf.writestr(f"Processed Data/Plate {pidx}/Conversion.csv", df.to_csv(index=False).encode("utf-8"))
            df = tables.get("yield")
            if df is not None and not df.empty:
                zf.writestr(f"Processed Data/Plate {pidx}/Yield.csv", df.to_csv(index=False).encode("utf-8"))
            df = tables.get("pies")
            if df is not None and not df.empty:
                zf.writestr(f"Processed Data/Plate {pidx}/PieFractions.csv", df.to_csv(index=False).encode("utf-8"))

            dfc = conf_tables.get(pidx)
            if dfc is not None and not dfc.empty:
                zf.writestr(f"Processed Data/Plate {pidx}/Confidence.csv", dfc.to_csv(index=False).encode("utf-8"))

        # Images (figures on-screen)
        kind_map = {"Product formation": "presence","Conversion": "conversion","Yield with IS": "yield","Pie charts (plate only)": "pies"}
        atype = st.session_state.get("analysis_type")
        wanted = kind_map.get(atype)
        imgs = st.session_state.get("image_bytes", {}) or {}
        any_written = False
        if imgs:
            if wanted:
                for fn, b in imgs.items():
                    if f"_{wanted}" in fn and b:
                        zf.writestr(f"Images/{fn}", b); any_written = True
            for fn, b in imgs.items():
                if "_confidence" in fn and b:
                    zf.writestr(f"Images/{fn}", b); any_written = True

        if not any_written:
            tmpdir = st.session_state.get("tmpdir")
            if tmpdir and os.path.isdir(tmpdir):
                for fn in sorted(os.listdir(tmpdir)):
                    if fn.lower().endswith((".png", ".jpg", ".jpeg")):
                        fpath = os.path.join(tmpdir, fn)
                        try:
                            with open(fpath, "rb") as fh:
                                zf.writestr(f"Images/{fn}", fh.read())
                        except Exception:
                            pass
    return buf.getvalue()

if st.session_state.get("zip_dirty"):
    try:
        st.session_state.last_zip_payload = build_results_zip_from_state()
    except Exception:
        st.session_state.last_zip_payload = None
    finally:
        st.session_state.zip_dirty = False

zip_base = st.session_state.get("zip_basename", "results")
payload = st.session_state.get("last_zip_payload")
st.download_button(
    label=f"Download Results",
    data=(payload or b""),
    file_name=f"{zip_base}.zip",
    disabled=(payload is None),
    help=None if payload else "Make a change or press 'Update results' so a figure renders, then try again."
)

st.markdown("---")
if st.button("Reset / Clear results"):
    if st.session_state.tmpdir and os.path.isdir(st.session_state.tmpdir):
        cleanup_tmp_session_dir(st.session_state.tmpdir)
    st.session_state.analysis_ready = False
    st.session_state.need_recompute = False
    st.session_state.tmpdir = None
    st.session_state.plates = {}
    st.session_state.conf_tables = {}
    st.session_state.last_zip_payload = None
    st.session_state.raw = None
    st.session_state.sproc_display = None
    st.session_state.mw_table = None
    st.session_state.tmerge = None
    st.session_state.sample_role_map_cached = {}
    st.session_state.current_editor_df = None
    st.session_state.do_update_roles = False
    st.experimental_rerun()





