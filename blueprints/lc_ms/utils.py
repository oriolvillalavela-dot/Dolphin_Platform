import os
import json
import pandas as pd
from sqlalchemy import text
from models import Chemist, ELN, IPCMeasurement, PurifMeasurement
from database import SessionLocal
import threading
import time

# Constants
APP_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DATA_DIR = os.path.join(APP_DIR, "data")
PURIF_METHODS_JSON = os.path.join(DATA_DIR, "purif_methods.json")
EXCEL_SEED = os.path.join(DATA_DIR, "LCMS_database.xlsx")
IPC_SEED = os.path.join(DATA_DIR, "IPC_measurements.xlsx")
PURIF_SEED = os.path.join(DATA_DIR, "Purification_measurements.xlsx")

DEFAULT_PURIF_METHODS = [
    "ISCO", "Extraction", "RP-HPLC", "Crystallization", "Evaporation",
    "Distillation", "Filtration", "Precipitation", "Metal Scavenging",
    "Solid phase Extraction",
]

DEFAULT_USER_MAP = {
    "villalao": "OV", "boddya": "AB", "nippad": "DN", "wolfardj": "JW",
    "chettatn": "NC", "stenzhoy": "YS", "martj336": "JM",
}

def load_purif_methods() -> list[str]:
    try:
        if os.path.exists(PURIF_METHODS_JSON):
            with open(PURIF_METHODS_JSON, "r", encoding="utf-8") as f:
                items = json.load(f)
                if isinstance(items, list): return [str(x) for x in items if str(x).strip()]
    except Exception:
        pass
    seen, out = set(), []
    for m in DEFAULT_PURIF_METHODS:
        if m.lower() not in seen:
            out.append(m); seen.add(m.lower())
    return out

def save_purif_methods(methods: list[str]) -> None:
    try:
        os.makedirs(DATA_DIR, exist_ok=True)
        with open(PURIF_METHODS_JSON, "w", encoding="utf-8") as f:
            json.dump(methods, f, ensure_ascii=False, indent=2)
    except Exception:
        pass

def get_user_id(chemist_username: str) -> str:
    session = SessionLocal()
    try:
        c = session.query(Chemist).filter_by(username=(chemist_username or "").strip().lower()).first()
        return c.user_id if c else None
    finally:
        session.close()

def validate_eln_inputs(form):
    eln_id = form.get("eln_id","").strip()
    chemist = form.get("chemist","").strip()
    chemforms = [form.get(n,"").strip() for n in ["stmat_1_chemform","stmat_2_chemform","product_1_chemform","product_2_chemform","product_3_chemform","product_4_chemform"]]
    errors = []
    if not eln_id: errors.append("ELN ID is required.")
    if not chemist: errors.append("Chemist is required.")
    if not any(chemforms): errors.append("At least one molecular formula must be provided.")
    return errors

def seed_chemists_if_empty():
    session = SessionLocal()
    try:
        if session.query(Chemist).count() == 0:
            session.add_all([Chemist(username=u, user_id=uid) for u, uid in DEFAULT_USER_MAP.items()])
            session.commit()
    finally:
        session.close()

def _seed_from_excels_safe():
    session = SessionLocal()
    try:
        # ---------------- Seed ELNs ----------------
        if session.query(ELN).count() == 0 and os.path.exists(EXCEL_SEED):
            try:
                df = pd.read_excel(EXCEL_SEED)
                expected_cols = ["ID","ELN_id","Chemist","stmat_1_chemform","stmat_2_chemform",
                                  "product_1_chemform","product_2_chemform","product_3_chemform","product_4_chemform"]
                df_cols_lower = {c.lower(): c for c in df.columns}
                renamed = {}
                for col in expected_cols:
                    key = col.lower()
                    if key in df_cols_lower:
                        renamed[df_cols_lower[key]] = col
                df = df.rename(columns=renamed)
                for m in [c for c in expected_cols if c not in df.columns]:
                    df[m] = None
                df = df[expected_cols].dropna(subset=["ELN_id"]).drop_duplicates(subset=["ELN_id"], keep="last")

                records = []
                for _, row in df.iterrows():
                    order_val = None
                    raw = row.get("ID", None)
                    if pd.notna(raw) and str(raw).strip():
                        try:
                            order_val = int(float(str(raw).strip()))
                        except Exception:
                            order_val = None

                    records.append(ELN(
                        eln_id=str(row["ELN_id"]).strip(),
                        chemist=str(row["Chemist"]).strip().lower(),
                        stmat_1_chemform=(None if pd.isna(row["stmat_1_chemform"]) else str(row["stmat_1_chemform"])),
                        stmat_2_chemform=(None if pd.isna(row["stmat_2_chemform"]) else str(row["stmat_2_chemform"])),
                        product_1_chemform=(None if pd.isna(row["product_1_chemform"]) else str(row["product_1_chemform"])),
                        product_2_chemform=(None if pd.isna(row["product_2_chemform"]) else str(row["product_2_chemform"])),
                        product_3_chemform=(None if pd.isna(row["product_3_chemform"]) else str(row["product_3_chemform"])),
                        product_4_chemform=(None if pd.isna(row["product_4_chemform"]) else str(row["product_4_chemform"])),
                        order_id=order_val,
                    ))
                if records:
                    session.add_all(records)
                    session.commit()

                    max_id = session.query(func.coalesce(func.max(ELN.order_id), 0)).scalar() or 0
                    missing = session.query(ELN).filter(ELN.order_id.is_(None)).order_by(ELN.eln_id.asc()).all()
                    if missing:
                        for i, rec in enumerate(missing, start=1):
                            rec.order_id = max_id + i
                        session.commit()

            except Exception as e:
                print(f"Failed to seed ELNs from Excel: {e}")

        # ---------------- Seed IPC measurements ----------------
        if session.query(IPCMeasurement).count() == 0 and os.path.exists(IPC_SEED):
            try:
                df = pd.read_excel(IPC_SEED)
                import re
                def norm(s: str) -> str:
                    return re.sub(r'[^a-z0-9]+', ' ', str(s).lower()).strip()

                cols = { norm(c): c for c in df.columns }
                def col(*names):
                    for n in names:
                        c = cols.get(norm(n))
                        if c: return c
                    return None

                c_eln  = col("eln id", "eln_id", "eln")
                c_ipc  = col("ipc no.", "ipc_no", "ipc no", "ipc")
                c_dur  = col("duration (h)", "duration_h", "duration")
                c_user = col("user", "chemist", "chemist_username")
                c_method = col("lc-ms method (min)", "lc_ms_method_min", "method (min)")
                c_instr  = col("lc-ms instrument",   "lc_ms_instrument", "instrument")
                c_file   = col("lc-ms file name",    "lc_ms_file_name",  "file name")
                c_result = col("ipc result",         "result",           "ipc_result")

                if c_eln and c_ipc and c_dur:
                    for _, row in df.iterrows():
                        try:
                            eln_id     = str(row[c_eln]).strip()
                            ipc_no     = int(row[c_ipc])
                            duration_h = int(row[c_dur])
                        except Exception:
                            continue

                        chem_u = None
                        if c_user is not None:
                            val = row[c_user]
                            if pd.notna(val) and str(val).strip():
                                chem_u = str(val).strip().lower()
                        if not chem_u:
                            rec = session.query(ELN).get(eln_id)
                            chem_u = rec.chemist if rec else "unknown"

                        if session.query(IPCMeasurement).filter_by(eln_id=eln_id, ipc_no=ipc_no).first():
                            continue

                        m = IPCMeasurement(
                            chemist_username=chem_u,
                            eln_id=eln_id,
                            ipc_no=ipc_no,
                            duration_h=duration_h,
                            lc_ms_method_min=(str(row[c_method]).strip() if c_method and pd.notna(row[c_method]) else None),
                            lc_ms_instrument=(str(row[c_instr]).strip()  if c_instr  and pd.notna(row[c_instr])  else None),
                            lc_ms_file_name=(str(row[c_file]).strip()    if c_file   and pd.notna(row[c_file])   else None),
                            ipc_result=(str(row[c_result]).strip()       if c_result and pd.notna(row[c_result]) else None),
                        )
                        session.add(m)
                    session.commit()
            except Exception as e:
                print(f"Failed to seed IPC measurements: {e}")

        # ---------------- Seed PURIF measurements ----------------
        if session.query(PurifMeasurement).count() == 0 and os.path.exists(PURIF_SEED):
            try:
                df = pd.read_excel(PURIF_SEED)
                import re
                def norm(s: str) -> str:
                    return re.sub(r'[^a-z0-9]+', ' ', str(s).lower()).strip()

                cols = {norm(c): c for c in df.columns}
                def pick(*names):
                    for n in names:
                        c = cols.get(norm(n))
                        if c: return c
                    return None

                c_user = pick("user","username","chemist","chemist_username")
                c_eln  = pick("eln id","eln_id","eln","eln-id","elnid")
                c_pno  = pick("purification no.","purif no.","purif_no","purification","purif")
                c_frac = pick("fraction no.","fraction","fraction_no","fraction number","fractionno")
                c_method = pick("purif method","purification method","method","method name")
                c_instr  = pick("analysis instrument","instrument","lc ms instrument","lc ms","lc ms instr")
                c_file   = pick("analysis file name","file name","filename","lc ms file","lc ms filename")
                c_result = pick("purif result","result","status","outcome")

                def first_int(v):
                    if pd.isna(v): return None
                    m = re.search(r'-?\d+', str(v).strip())
                    return int(m.group()) if m else None

                for _, row in df.iterrows():
                    eln_id = (str(row[c_eln]).strip() if c_eln and pd.notna(row[c_eln]) else None)
                    purif_no = first_int(row[c_pno]) if c_pno else None
                    fraction_label = None
                    if c_frac and pd.notna(row[c_frac]):
                        fraction_label = str(row[c_frac]).strip()
                    fraction_no = first_int(fraction_label) if fraction_label is not None else None

                    if not eln_id or purif_no is None or fraction_no is None:
                        continue

                    chem_u = None
                    if c_user and pd.notna(row[c_user]) and str(row[c_user]).strip():
                        chem_u = str(row[c_user]).strip().lower()
                    if not chem_u:
                        rec = session.query(ELN).get(eln_id)
                        chem_u = rec.chemist if rec else "unknown"

                    if session.query(PurifMeasurement).filter_by(eln_id=eln_id, purif_no=purif_no, fraction_no=fraction_no).first():
                        continue

                    session.add(PurifMeasurement(
                        chemist_username=chem_u, eln_id=eln_id, purif_no=purif_no,
                        fraction_no=fraction_no, fraction_label=fraction_label,
                        purif_method=(str(row[c_method]).strip() if c_method and pd.notna(row[c_method]) else None),
                        analysis_instrument=(str(row[c_instr]).strip() if c_instr and pd.notna(row[c_instr]) else None),
                        analysis_file_name=(str(row[c_file]).strip() if c_file and pd.notna(row[c_file]) else None),
                        purif_result=(str(row[c_result]).strip() if c_result and pd.notna(row[c_result]) else None),
                    ))
                session.commit()
            except Exception as e:
                print(f"Failed to seed PURIF measurements: {e}")
    finally:
        session.close()

def init_lcms_data():
    seed_chemists_if_empty()
    _seed_from_excels_safe()
