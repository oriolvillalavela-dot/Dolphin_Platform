from typing import Dict, Optional, Tuple, List
import math
import pandas as pd

# We import only mw_from_formula from utils; everything else is self-contained here.
# Use package-safe imports to avoid collisions with top-level modules.
try:
    from .utils import mw_from_formula
except Exception:
    try:
        from LCMS_Analysis_Tool.app.utils import mw_from_formula
    except Exception:
        # Inline fallback: used when utils cannot be imported (e.g. missing molmass).
        # mw_from_formula returning None is handled gracefully at all call sites.
        def mw_from_formula(formula: str):  # type: ignore[misc]
            return None


# ------------------------------- Helpers & Basics -------------------------------

def classify_sample_id(sample_id: str) -> Dict[str, Optional[str]]:
    import re
    s = (sample_id or "").strip()
    out = {
        "class":"Unknown","eln_id":None,"plate_no":None,"ipc_no":None,
        "reaction_h":None,"temp_C":None,"purif_no":None,"fraction_no":None,
        "chem_id":None,"bottle_no":None
    }
    m = re.match(r"^ELN(\d{6})-(\d{3})-(\d{3})_(\d{1,3})h(\d{1,3})C_([A-H]\d{1,2})$", s, re.IGNORECASE)
    if m:
        out.update({
            "class":"Library",
            "eln_id":f"ELN{m.group(1)}-{m.group(2)}-{m.group(3)}",
            "reaction_h":int(m.group(4)),
            "temp_C":int(m.group(5))
        })
        return out
    m = re.match(r"^ELN(\d{6})-(\d{3})_(\d+?)_(\d+?)_(\d{1,3})h(\d{1,3})C_([A-H]\d{1,2})$", s, re.IGNORECASE)
    if m:
        out.update({
            "class":"Screening",
            "eln_id":f"ELN{m.group(1)}-{m.group(2)}",
            "plate_no":int(m.group(3)),
            "ipc_no":int(m.group(4)),
            "reaction_h":int(m.group(5)),
            "temp_C":int(m.group(6))
        })
        return out
    m = re.match(r"^ELN(\d{6})-(\d{3})_(\d+?)_(\d{1,3})h$", s, re.IGNORECASE)
    if m:
        out.update({
            "class":"IPC",
            "eln_id":f"ELN{m.group(1)}-{m.group(2)}",
            "ipc_no":int(m.group(3)),
            "reaction_h":int(m.group(4))
        })
        return out
    m = re.match(r"^ELN(\d{6})-(\d{3})_(\d+?)_F(\d+)$", s, re.IGNORECASE)
    if m:
        out.update({
            "class":"Purification",
            "eln_id":f"ELN{m.group(1)}-{m.group(2)}",
            "purif_no":int(m.group(3)),
            "fraction_no":int(m.group(4))
        })
        return out
    m = re.match(r"^Chem_(\d+)_B(\d+)$", s, re.IGNORECASE)
    if m:
        out.update({
            "class":"QC",
            "chem_id":f"Chem_{m.group(1)}",
            "bottle_no":int(m.group(2))
        })
        return out
    return out


def build_raw_tables(sample_df: pd.DataFrame, peak_df: pd.DataFrame, mass_df: pd.DataFrame):
    """Return the trio of raw tables with canonical column names for display/export."""
    s = sample_df.rename(
        columns={
            "measurement_id": "Measurement-ID",
            "sample_id": "Sample-ID",
            "method": "Method",
            "date": "Date",
            "time": "Time",
            "username": "Username",
            "well": "Well",
            "run_order": "RunOrder",
        }
    )
    return {
        "Raw_Sample_Data": s,
        "Raw_Peak_Data": peak_df.copy(),
        "Raw_Mass_Data": mass_df.copy(),
    }


# ------------------------------- Core linking logic -----------------------------

def _norm_channel(ch: str) -> str:
    c = (str(ch) or "").upper().strip()
    if "ESI" in c or "MS " in c or c in ("ES+","ES-"):
        return "ES+" if "+" in c else "ES-"
    if "DAD" in c or "TAC" in c or "UV" in c or "220" in c:
        return "TAC"
    return c  # pass-through


def _topN(masses: List[Dict], n: int = 5) -> List[Dict]:
    if not masses:
        return []
    ok = [d for d in masses if isinstance(d, dict) and d.get("mass") is not None]
    ok.sort(key=lambda d: (d.get("intensity") or 0.0), reverse=True)
    return ok[:n]


def _group_ms_by_key(mass_df: pd.DataFrame) -> Dict[Tuple, List[Dict]]:
    """
    Group MS rows by (measurement_id, sample_id, peak_id) and keep
    list of dicts [{mass, intensity, channel}, ...].

    Robust to missing/odd channels:
      - measurement_id, sample_id normalized to str.strip()
      - peak_id to numeric
      - channel normalized when present; if missing, kept as None (no filtering)
    """
    if mass_df is None or mass_df.empty:
        return {}
    m = mass_df.copy()

    # Normalize IDs
    if "measurement_id" in m.columns:
        m["measurement_id"] = m["measurement_id"].astype(str).str.strip()
    if "sample_id" in m.columns:
        m["sample_id"] = m["sample_id"].astype(str).str.strip()

    # Normalize peak_id & intensity
    m["peak_id"] = pd.to_numeric(m.get("peak_id"), errors="coerce")
    m = m.dropna(subset=["peak_id", "mass"]).copy()
    m["mass"] = pd.to_numeric(m["mass"], errors="coerce")
    m["intensity"] = pd.to_numeric(m.get("intensity", 0.0), errors="coerce").fillna(0.0)

    # Channel: normalize if present; don't filter out unknowns
    if "channel" in m.columns:
        m["channel"] = m["channel"].apply(_norm_channel)
    else:
        m["channel"] = None

    def _pack(grp: pd.DataFrame) -> List[Dict]:
        rows = []
        for _, r in grp.iterrows():
            rows.append({
                "mass": float(r["mass"]),
                "intensity": float(r["intensity"]) if pd.notna(r["intensity"]) else 0.0,
                "channel": r.get("channel", None),
            })
        rows.sort(key=lambda d: d["intensity"], reverse=True)
        return rows

    key = ["measurement_id","sample_id","peak_id"]
    out = {}
    for k, grp in m.groupby(key, dropna=False):
        out[k] = _pack(grp)
    return out


def _collect_rpt_results(mass_df: pd.DataFrame) -> Dict[Tuple, List[Dict]]:
    """
    Optional: if mass_df contains parsed RPT [RESULTS]-level info, collect:
      result_mass (float), result_confirmed (bool/int), %BPI if available.
    """
    if mass_df is None or mass_df.empty:
        return {}
    cols = set(mass_df.columns)
    needed = {"result_mass","result_confirmed"}
    if not needed.issubset(cols):
        return {}

    df = mass_df.dropna(subset=["result_mass"]).copy()

    # Normalize IDs
    if "measurement_id" in df.columns:
        df["measurement_id"] = df["measurement_id"].astype(str).str.strip()
    if "sample_id" in df.columns:
        df["sample_id"] = df["sample_id"].astype(str).str.strip()

    if "channel" in df.columns:
        df["channel"] = df["channel"].apply(_norm_channel)
    else:
        df["channel"] = None

    key = ["measurement_id","sample_id","peak_id"]
    bag = {}
    for k, grp in df.groupby(key, dropna=False):
        L = []
        for _, r in grp.iterrows():
            L.append({
                "mass": float(r["result_mass"]),
                "confirmed": bool(int(r["result_confirmed"])) if pd.notna(r["result_confirmed"]) else False,
                "bpi": float(r["result_bpi"]) if "result_bpi" in df.columns and pd.notna(r["result_bpi"]) else None,
                "channel": r["channel"],
            })
        bag[k] = L
    return bag


def link_peaks_exact(peak_df: pd.DataFrame, mass_df: pd.DataFrame) -> pd.DataFrame:
    """
    EXACT-ID LINKING (your workflow):
      1) Take TAC-like rows from peak_df (normalized to 'TAC').
      2) Build an ms_map keyed by (measurement_id, sample_id, peak_id) from mass_df.
      3) For each TAC row, attach all masses from the same key; sort by intensity; keep top 5.
    """
    if peak_df is None or peak_df.empty:
        return pd.DataFrame(columns=[
            "measurement_id","sample_id","peak_id","rt_min","peak_area","channel","masses"
        ])

    p = peak_df.copy()

    # Normalize IDs and channel on peak table
    if "measurement_id" in p.columns:
        p["measurement_id"] = p["measurement_id"].astype(str).str.strip()
    if "sample_id" in p.columns:
        p["sample_id"] = p["sample_id"].astype(str).str.strip()
    p["channel_norm"] = p["channel"].apply(_norm_channel)
    p["peak_id"] = pd.to_numeric(p["peak_id"], errors="coerce")

    # TAC rows (accept TAC/DAD/UV/220 via normalization)
    tac = p[p["channel_norm"] == "TAC"][["measurement_id","sample_id","peak_id","rt_min","peak_area"]].copy()
    tac["channel"] = "TAC"

    # Build mass map strictly by key (robust to channel quirks)
    ms_map = _group_ms_by_key(mass_df)

    def _attach(row):
        k = (row["measurement_id"], row["sample_id"], row["peak_id"])
        lst = list(ms_map.get(k, []))
        lst.sort(key=lambda d: (d.get("intensity") or 0.0), reverse=True)
        return lst[:5]

    tac["masses"] = tac.apply(_attach, axis=1)
    return tac[["measurement_id","sample_id","peak_id","rt_min","peak_area","channel","masses"]]


def proximity_assign(
    tac_df: pd.DataFrame,
    peak_df: pd.DataFrame,
    mass_df: pd.DataFrame,
    rt_tol: float = 0.05,
) -> pd.DataFrame:
    """
    For TAC rows with empty 'masses', link to nearest ES+ and ES- peaks within ±rt_tol,
    then pull their MS lists and keep top-5 combined.
    Uses the same robust ms_map as exact linking.
    """
    if tac_df is None or tac_df.empty:
        return tac_df

    # Normalize peak_df for ESI pool
    p = peak_df.copy()
    if "measurement_id" in p.columns:
        p["measurement_id"] = p["measurement_id"].astype(str).str.strip()
    if "sample_id" in p.columns:
        p["sample_id"] = p["sample_id"].astype(str).str.strip()
    p["channel_norm"] = p["channel"].apply(_norm_channel)
    p["peak_id"] = pd.to_numeric(p["peak_id"], errors="coerce")
    esi = p[p["channel_norm"].isin(["ES+","ES-"]) & p["rt_min"].notna()].copy()

    # Build MS map on normalized keys
    ms_map = _group_ms_by_key(mass_df)

    out = tac_df.copy()
    if "measurement_id" in out.columns:
        out["measurement_id"] = out["measurement_id"].astype(str).str.strip()
    if "sample_id" in out.columns:
        out["sample_id"] = out["sample_id"].astype(str).str.strip()
    out["proximity_matched"] = False

    for (meas, sid), grp in out.groupby(["measurement_id","sample_id"], dropna=False):
        idxs = grp.index
        pool = esi[(esi["measurement_id"]==meas) & (esi["sample_id"]==sid)]
        if pool.empty:
            continue
        for i in idxs:
            if out.at[i, "masses"]:
                continue  # already exact-linked
            rt = out.at[i, "rt_min"]
            if pd.isna(rt):
                continue
            cand = pool[pool["rt_min"].between(rt - rt_tol, rt + rt_tol)]
            if cand.empty:
                continue

            appended = []
            # pick nearest ES+ and nearest ES- independently
            for ch in ("ES+","ES-"):
                cc = cand[cand["channel_norm"] == ch]
                if cc.empty:
                    continue
                j = (cc["rt_min"] - rt).abs().idxmin()
                pid = cc.at[j, "peak_id"]
                k = (meas, sid, pid)
                appended.extend(ms_map.get(k, []))

            if appended:
                appended.sort(key=lambda d: (d.get("intensity") or 0.0), reverse=True)
                out.at[i, "masses"] = appended[:5]
                out.at[i, "proximity_matched"] = True

    return out


# ------------------------------- Role assignment -------------------------------

def _expected_adducts(formula: str, sign: str) -> List[Tuple[str, float]]:
    """
    Return list of (adduct_label, expected_mz) for a formula and sign.
    - ES+: [M+H]+ and [M+2H]2+ (double charge)
    - ES-: [M-H]-
    RDKit MW rounded to 2 decimals FIRST, then adduct arithmetic.
    """
    if not formula:
        return []
    mw = mw_from_formula(formula)
    if mw is None:
        return []
    mw_r = round(float(mw), 2)

    if sign == "ES+":
        return [
            ("[M+H]+",   mw_r + 1.0),
            ("[M+2H]2+", mw_r / 2.0 + 1.0),
        ]
    if sign == "ES-":
        return [
            ("[M-H]-", mw_r - 1.0),
        ]
    return []


def _build_expected_mass_index(sample_role_map: dict) -> Dict[str, List[Tuple[str, str, str, float]]]:
    """
    Build per-sample expected masses:
      { sample_id: [(role_label, "ES+", adduct_label, m/z), ...] }
    """
    out = {}
    for sid, pairs in (sample_role_map or {}).items():
        arr = []
        for formula, role_label in pairs:
            for sign in ("ES+","ES-"):
                for adduct_label, mz in _expected_adducts(formula, sign):
                    arr.append((role_label, sign, adduct_label, mz))
        out[sid] = arr
    return out


def _match_role_by_rpt_confirmed(row, exp_index, mz_tol_da=0.5):
    """
    Try to assign role using RPT-confirmed masses if available.
    Returns (role_label, source, adduct, rank, rel_intensity) or (None, None, None, None, None).
    """
    sid = str(row.get("sample_id","")).strip()
    exp_list = exp_index.get(sid) or []
    if not exp_list:
        return (None, None, None, None, None)

    results = row.get("rpt_results") or []
    if not results:
        return (None, None, None, None, None)

    for role_label, sign, adduct, expected_mz in exp_list:
        for res in results:
            if not res.get("confirmed"):
                continue
            if sign and res.get("channel") and _norm_channel(res["channel"]) != sign:
                continue
            m = res.get("mass")
            if m is None:
                continue
            if abs(m - expected_mz) <= mz_tol_da:
                return (role_label, "rpt_confirmed", adduct, None, 1.0)
    return (None, None, None, None, None)


def _match_role_by_top5_masses(row, exp_index, mz_tol_da=0.5):
    """
    Fallback: scan top-5 MS masses (already trimmed) and try to match expected masses
    (including adduct variants). Prefer channel-consistent matches.
    Returns (role_label, source, adduct, rank, rel_intensity) or (None, None, None, None, None).
    """
    sid = str(row.get("sample_id","")).strip()
    exp_list = exp_index.get(sid) or []
    if not exp_list:
        return (None, None, None, None, None)

    masses = row.get("masses") or []
    if not masses:
        return (None, None, None, None, None)

    top_int = max((d.get("intensity") or 0.0) for d in masses) or 1.0

    for role_label, sign, adduct, expected_mz in exp_list:
        for idx, d in enumerate(masses, start=1):
            ch = _norm_channel(d.get("channel","")) if d.get("channel") else None
            if ch and sign and ch != sign:
                continue
            m = d.get("mass")
            if m is None:
                continue
            if abs(m - expected_mz) <= mz_tol_da:
                rel = (d.get("intensity") or 0.0) / top_int
                return (role_label, "mz_top_k", adduct, idx, rel)

    return (None, None, None, None, None)


def _confidence_from_source(source: Optional[str], rank: Optional[int], rel_intensity: Optional[float], adduct: Optional[str]) -> float:
    """
    Heuristic confidence (0–100):
      - rpt_confirmed -> 95
      - mz_top_k -> blend of rank and relative intensity
          base = 30 + 40*((6 - rank)/5) + 30*(rel_intensity)
          adduct tweak: [M+H]+ preferred over [M+2H]2+  (penalize 2+ by ~10 points)
    """
    if source == "rpt_confirmed":
        return 95.0
    if source == "mz_top_k":
        r = max(1, min(5, int(rank or 5)))
        ri = max(0.0, min(1.0, float(rel_intensity or 0.0)))
        score = 30.0 + 40.0 * ((6 - r) / 5.0) + 30.0 * ri
        if adduct == "[M+2H]2+":
            score -= 10.0
        return max(0.0, min(100.0, score))
    return 0.0


def auto_assign_roles_per_sample(
    tac_df: pd.DataFrame,
    sample_role_map: dict,
    mz_tol_da: float = 0.5,
    dedupe_per_role: bool = True,
) -> pd.DataFrame:
    """
    Assign roles per sample using (a) RPT-confirmed matches if available,
    else (b) top-5 mass matches (MW rounded to 2 dp, adducts incl., ±0.5 Da).
    Adds columns:
      role, role_source, found_adduct, match_rank, rel_intensity, confidence_score
    """
    if tac_df is None or tac_df.empty:
        return tac_df

    exp_index = _build_expected_mass_index(sample_role_map)

    rows = []
    for _, row in tac_df.iterrows():
        role_label, source, adduct, rank, rel = _match_role_by_rpt_confirmed(row, exp_index, mz_tol_da)
        if role_label is None:
            role_label, source, adduct, rank, rel = _match_role_by_top5_masses(row, exp_index, mz_tol_da)

        out = dict(row)
        out["role"] = role_label
        out["role_source"] = source
        out["found_adduct"] = adduct
        out["match_rank"] = rank
        out["rel_intensity"] = rel
        out["confidence_score"] = _confidence_from_source(source, rank, rel, adduct)
        rows.append(out)

    out_df = pd.DataFrame(rows)

    if dedupe_per_role:
        # Keep largest TAC area per (measurement, sample, role)
        keep_mask = out_df["role"].notna()
        sub = out_df[keep_mask].copy()
        sub = sub.sort_values(
            ["measurement_id","sample_id","role","peak_area"],
            ascending=[True, True, True, False]
        ).drop_duplicates(subset=["measurement_id","sample_id","role"], keep="first")
        other = out_df[~keep_mask]
        out_df = pd.concat([sub, other], ignore_index=True)

    return out_df


# ------------------------------- Analysis helpers -------------------------------

def conversion_pct(a_sm: Optional[float], a_prod: Optional[float]) -> float:
    a = (a_sm or 0.0)
    b = (a_prod or 0.0)
    d = a + b
    return (b / d * 100.0) if d > 0 else float("nan")


def yield_with_is(
    area_prod: float,
    area_is: float,
    conc_is_mM: float,
    rf: float,
    volume_mL: float,
    mw_product: float,
    scale_mmol: float,
):
    if (
        not area_prod
        or not area_is
        or area_is == 0
        or not rf
        or rf == 0
        or not mw_product
    ):
        return {
            "conc_product_mM": float("nan"),
            "obtained_mg": float("nan"),
            "expected_mg": float("nan"),
            "yield_pct": float("nan"),
        }
    conc_prod_mM = (area_prod / area_is) * (conc_is_mM / rf)
    expected_mg = mw_product * scale_mmol
    obtained_mg = conc_prod_mM * volume_mL * mw_product * 2 / 1000.0 
    yld = (obtained_mg / expected_mg * 100.0) if expected_mg else float("nan")
    return {
        "conc_product_mM": conc_prod_mM,
        "obtained_mg": obtained_mg,
        "expected_mg": expected_mg,
        "yield_pct": yld,
    }
