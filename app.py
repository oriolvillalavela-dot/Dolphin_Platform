import os
import sys
import re
from datetime import datetime, date

# Ensure the project root is on sys.path so LCMS_Analysis_Tool imports work
# regardless of the working directory when the app is started (e.g. Docker/Gunicorn).
_project_root = os.path.dirname(os.path.abspath(__file__))
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

from flask import Flask, render_template, request, jsonify, redirect, url_for
from dotenv import load_dotenv
load_dotenv()
from sqlalchemy import create_engine, func, or_, text as sq_text, cast, String, Integer, update
from sqlalchemy.engine import URL
from sqlalchemy.orm import sessionmaker, scoped_session
from models import Base, Chemical, Supplier, Bottle, Batch, Plate, PlateWellReagent, SurfRow, QCResult, Experiment, ExperimentDetails, PlateDesign, ProcessingJob, MoleculeStatus, ProjectTeamMember, MoleculeSmiles, Screening, ScreeningPlateDesign
from sqlalchemy import func, or_, cast, String, literal
from types import SimpleNamespace
from flask import current_app
from sqlalchemy import text 
from sqlalchemy import Column, Integer, String, JSON
from utils.chem_converter.cas_client import CASClient
from utils.chem_converter.converters import iupac_to_kekule_smiles
from rdkit import Chem
from rdkit.Chem import Descriptors, rdMolDescriptors
from blueprints.lc_ms import lc_ms_bp
from blueprints.lc_ms.api import lc_ms_api_bp
from blueprints.lcms_qc import lcms_qc_bp
from blueprints.lcms_analyser import lcms_analyser_bp
import pandas as pd
from utils.chem_utils import compute_functional_groups, generate_structure_svg, generate_pdf_export, FUNCTIONAL_GROUPS

app = Flask(__name__)
app.register_blueprint(lc_ms_bp)
app.register_blueprint(lc_ms_api_bp)
app.register_blueprint(lcms_qc_bp)
app.register_blueprint(lcms_analyser_bp)
from blueprints.ppm import ppm_bp, ppm_api_bp
app.register_blueprint(ppm_bp)
app.register_blueprint(ppm_api_bp)
from blueprints.screenings import screenings_bp, screenings_api_bp, plate_designs_api_bp
app.register_blueprint(screenings_bp)
app.register_blueprint(screenings_api_bp)
app.register_blueprint(plate_designs_api_bp)
app.secret_key = os.getenv("SECRET_KEY", "change_me")
cas_client = CASClient()

from database import engine, SessionLocal

# --- Schema bootstrap + lightweight migration ---
_schema_ready = False

def ensure_schema():
    global _schema_ready
    if _schema_ready:
        return

    # Create tables from models if they don't exist
    Base.metadata.create_all(engine)

    # Lightweight migrations
    with engine.begin() as conn:
        # SURF columns (idempotent)
        for col, ddl in [
            ("eln_id", "TEXT"),
            ("plate_no", "INTEGER"),
            ("well", "TEXT"),
            ("mixing", "TEXT"),
            ("atmosphere", "TEXT"),
            ("wavelength_nm", "DOUBLE PRECISION"),
            ("scale_mol", "DOUBLE PRECISION"),
            ("concentration_mol_l", "DOUBLE PRECISION"),
            ("startingmat_1_id", "TEXT"),
            ("startingmat_1_eq", "DOUBLE PRECISION"),
            ("startingmat_2_id", "TEXT"),
            ("startingmat_2_eq", "DOUBLE PRECISION"),
            ("reagent_1_id", "TEXT"),
            ("reagent_1_eq", "DOUBLE PRECISION"),
            ("reagent_2_id", "TEXT"),
            ("reagent_2_eq", "DOUBLE PRECISION"),
            ("reagent_3_id", "TEXT"),
            ("reagent_3_eq", "DOUBLE PRECISION"),
            ("reagent_4_id", "TEXT"),
            ("reagent_4_eq", "DOUBLE PRECISION"),
            ("reagent_5_id", "TEXT"),
            ("reagent_5_eq", "DOUBLE PRECISION"),
            ("solvent_1_id", "TEXT"),
            ("solvent_1_fraction", "DOUBLE PRECISION"),
            ("solvent_2_id", "TEXT"),
            ("solvent_2_fraction", "DOUBLE PRECISION"),
            ("plate_id", "INTEGER"),
        ]:
            conn.execute(
                sq_text(f"ALTER TABLE IF EXISTS surf ADD COLUMN IF NOT EXISTS {col} {ddl}")
            )
# NEW: Migration for ExperimentDetails metadata
        for col, ddl in [
            ("atmosphere", "TEXT"),
            ("mixing", "TEXT"),
            ("wavelength_nm", "DOUBLE PRECISION"),
            ("startingmat_1_raw", "TEXT"),
            ("startingmat_2_raw", "TEXT"),
            ("reagent_1_raw", "TEXT"),
            ("reagent_2_raw", "TEXT"),
            ("reagent_3_raw", "TEXT"),
            ("reagent_4_raw", "TEXT"),
            ("reagent_5_raw", "TEXT"),
            ("solvent_1_raw", "TEXT"),
            ("solvent_2_raw", "TEXT"),
        ]:
            conn.execute(
                sq_text(f"ALTER TABLE IF EXISTS experimentdetails ADD COLUMN IF NOT EXISTS {col} {ddl}")
            )
# NEW: Migration for Experiment KPI columns
        for col, ddl in [
            ("completion_date", "DATE"),
            ("no_reactions", "INTEGER"),
            ("no_reg_compounds", "INTEGER"),
            ("success_rate", "DOUBLE PRECISION"),
        ]:
            conn.execute(
                sq_text(f"ALTER TABLE IF EXISTS experiments ADD COLUMN IF NOT EXISTS {col} {ddl}")
            )
        # Make concentration optional in batch_db
        try:
            conn.execute(
                sq_text("ALTER TABLE IF EXISTS batch_db ALTER COLUMN concentration_moll DROP NOT NULL")
            )
        except Exception:
            pass

        # NEW: add barcode column to bottle_db
        try:
            conn.execute(
                sq_text("ALTER TABLE IF EXISTS bottle_db ADD COLUMN IF NOT EXISTS barcode TEXT")
            )
        except Exception:
            pass

        # NEW: Migration for ChemDB Enhancements
        try:
            conn.execute(sq_text("ALTER TABLE IF EXISTS chemicals ADD COLUMN IF NOT EXISTS functional_groups JSON"))
            conn.execute(sq_text("ALTER TABLE IF EXISTS chemicals ADD COLUMN IF NOT EXISTS structure_svg TEXT"))
        except Exception:
            pass

        # NEW: Plate Designer
        try:
            conn.execute(sq_text("ALTER TABLE IF EXISTS plate_designs ADD COLUMN IF NOT EXISTS plate_metadata JSON"))
        except Exception:
            pass

        # NEW: Screenings
        try:
            conn.execute(sq_text("ALTER TABLE IF EXISTS screening_plate_designs ADD COLUMN IF NOT EXISTS axes JSON"))
            conn.execute(sq_text("ALTER TABLE IF EXISTS screenings ADD COLUMN IF NOT EXISTS manual_metadata JSON"))
            conn.execute(sq_text("ALTER TABLE IF EXISTS screenings ADD COLUMN IF NOT EXISTS eln_stmat_data JSON"))
            conn.execute(sq_text("ALTER TABLE IF EXISTS screenings ADD COLUMN IF NOT EXISTS eln_product_data JSON"))
            conn.execute(sq_text("ALTER TABLE IF EXISTS screenings ADD COLUMN IF NOT EXISTS scale TEXT"))
            conn.execute(sq_text("ALTER TABLE IF EXISTS screenings ADD COLUMN IF NOT EXISTS is_photochemistry BOOLEAN DEFAULT FALSE"))
            conn.execute(sq_text("ALTER TABLE IF EXISTS screenings ADD COLUMN IF NOT EXISTS wavelength_nm DOUBLE PRECISION"))
        except Exception:
            pass

        # NEW: PPM — Project Process Management
        # Tables are managed by SQLAlchemy create_all; this block handles any future column additions.
        try:
            conn.execute(sq_text("""
                CREATE TABLE IF NOT EXISTS ppm_processing_jobs (
                    id SERIAL PRIMARY KEY,
                    job_id VARCHAR(64) UNIQUE NOT NULL,
                    filename VARCHAR(512) NOT NULL,
                    upload_ts TIMESTAMP DEFAULT NOW(),
                    uploader VARCHAR(128),
                    status VARCHAR(32) DEFAULT 'pending',
                    error_msg TEXT,
                    week_date VARCHAR(32),
                    flagged_for_review BOOLEAN DEFAULT FALSE
                )
            """))
            conn.execute(sq_text("""
                CREATE TABLE IF NOT EXISTS ppm_molecule_statuses (
                    id SERIAL PRIMARY KEY,
                    job_id VARCHAR(64) NOT NULL REFERENCES ppm_processing_jobs(job_id) ON DELETE CASCADE,
                    project_id VARCHAR(64) NOT NULL,
                    theme_id VARCHAR(64) NOT NULL,
                    molecule_id VARCHAR(128) NOT NULL,
                    status VARCHAR(64) NOT NULL,
                    week_date VARCHAR(32),
                    page_number INTEGER,
                    CONSTRAINT uq_ppm_job_mol_page UNIQUE (job_id, molecule_id, page_number)
                )
            """))
            conn.execute(sq_text("""
                CREATE TABLE IF NOT EXISTS ppm_team_members (
                    id SERIAL PRIMARY KEY,
                    project_id VARCHAR(64) NOT NULL,
                    member_name VARCHAR(128) NOT NULL,
                    CONSTRAINT uq_ppm_project_member UNIQUE (project_id, member_name)
                )
            """))
            # Column additions (idempotent)
            conn.execute(sq_text(
                "ALTER TABLE IF EXISTS ppm_molecule_statuses ADD COLUMN IF NOT EXISTS structure_img TEXT"
            ))
            conn.execute(sq_text("""
                CREATE TABLE IF NOT EXISTS ppm_molecule_smiles (
                    id SERIAL PRIMARY KEY,
                    project_id VARCHAR(64) NOT NULL,
                    molecule_id VARCHAR(128) NOT NULL,
                    smiles TEXT NOT NULL,
                    structure_img TEXT,
                    updated_at TIMESTAMP DEFAULT NOW(),
                    CONSTRAINT uq_ppm_mol_smiles UNIQUE (project_id, molecule_id)
                )
            """))
        except Exception:
            pass

    # Backfill functional groups and SVG if missing OR if definitions changed
    try:
        session = SessionLocal()
        # Find chemicals with valid SMILES
        # We force update functional_groups because definitions might have changed
        chems_to_update = session.query(Chemical).filter(
            Chemical.smiles != None,
            Chemical.smiles != ""
        ).all()
        
        if chems_to_update:
            print(f"Re-computing functional groups for {len(chems_to_update)} chemicals...")
            for c in chems_to_update:
                # Always recompute functional groups
                c.functional_groups = compute_functional_groups(c.smiles)
                # Only compute SVG if missing (expensive)
                if not c.structure_svg:
                    c.structure_svg = generate_structure_svg(c.smiles)
            session.commit()
            print("Backfill/Update complete.")
        session.close()
        session.close()
    except Exception as e:
        print(f"Backfill failed: {e}")

    # Run LC-MS initial database seeding if needed
    try:
        from blueprints.lc_ms.utils import init_lcms_data
        init_lcms_data()
    except Exception as e:
        print(f"LC-MS Seed failed: {e}")

    _schema_ready = True

# ------------------- Batch Bulk Operations ------------------- #
@app.post("/batches/search_multiple")
def batches_search_multiple():
    session = SessionLocal()
    data = request.json or {}
    raw_ids = [x.strip() for x in data.get("ids", []) if x.strip()]
    if not raw_ids:
        return jsonify({"ok": True, "items": []})
        
    try:
        # Lowercase everything for comparison
        search_ids = [x.lower() for x in raw_ids]

        # Case-insensitive IN clause:
        # WHERE lower(batch_id) IN (...) OR lower(chem_id) IN (...)
        batches = session.query(Batch).filter(
            or_(
                func.lower(Batch.batch_id).in_(search_ids),
                func.lower(Batch.chem_id).in_(search_ids)
            )
        ).all()
        
        out = [
            {
                "batch_id": b.batch_id,
                "chem_id": b.chem_id,
                "location": b.location,
                "sublocation": b.sublocation,
                "status": b.status,
                "amount": b.amount
            }
            for b in batches
        ]
        return jsonify({"ok": True, "items": out})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500
    finally:
        session.close()



@app.before_request
def _ensure():
        if request.path in ("/health", "/favicon.ico"):
            return
        ensure_schema()

@app.teardown_appcontext
def shutdown_session(exception=None):
    SessionLocal.remove()

# -------- Helpers --------
def next_chem_id(session):
    try:
        val = session.execute(sq_text("""
            SELECT COALESCE(MAX((regexp_replace(chem_id, '^Chem_', '')::int)), 0)
            FROM chemicals
            WHERE chem_id ~ '^Chem_[0-9]+$'
        """)).scalar()
        return f"Chem_{int(val)+1}"
    except Exception:
        return "Chem_1"

def parse_float(s):
    """Safely converts Excel strings to float, handling empty values or 'NaN'."""
    if s is None or str(s).strip() == "" or str(s).lower() == "nan":
        return None
    try:
        return float(str(s).strip())
    except Exception:
        return None

def get_internal_id(session, identifier):
    """Standardizes input and reconciles against the chemicals database."""
    if not identifier: return None
    found = resolve_chem_identifier(session, identifier)
    if found: return found.chem_id
    
    try:
        mol_in = Chem.MolFromSmiles(identifier)
        if mol_in:
            can_in = Chem.MolToSmiles(mol_in, isomericSmiles=True)
            all_chems = session.query(Chemical).filter(Chemical.smiles != None).all()
            for c in all_chems:
                mol_db = Chem.MolFromSmiles(c.smiles)
                if mol_db and Chem.MolToSmiles(mol_db, isomericSmiles=True) == can_in:
                    return c.chem_id
    except: pass
    return identifier

def next_bottle_suffix(session, chem_id):
    maxn = 0
    for b in session.query(Bottle).filter(Bottle.chem_id == chem_id).all():
        try:
            n = int(b.bottle_id.split("_B")[-1])
            maxn = max(maxn, n)
        except Exception:
            pass
    return maxn + 1

def next_batch_suffix(session, chem_id, bottle_no, kind_letter):
    prefix = f"{chem_id}_B{bottle_no}_{kind_letter}"
    maxn = 0
    for bt in session.query(Batch).filter(Batch.batch_id.like(f"{prefix}%")).all():
        try:
            suf = int(bt.batch_id.split(f"_{kind_letter}")[-1])
            maxn = max(maxn, suf)
        except Exception:
            pass
    return maxn + 1

def parse_float(s):
    if s is None: return None
    try:
        return float(str(s).strip())
    except Exception:
        return None

def today():
    return date.today()

def resolve_chem_identifier(session, s):
    if not s: return None
    t = str(s).strip()
    chem = session.query(Chemical).filter(or_(Chemical.chem_id == t, func.lower(Chemical.chem_id) == t.lower())).first()
    if chem: return chem
    like = f"%{t.lower()}%"
    return (session.query(Chemical).filter(or_(
        func.lower(Chemical.common_name_abb).like(like),
        func.lower(Chemical.cas).like(like),
        func.lower(Chemical.smiles).like(like),
        func.lower(Chemical.inchi_key).like(like),
    )).first())

def _build_id_like(q: str) -> str | None:
    """
    Turn loose inputs like 'chem_b1_b1', 'CHEM_1_b1', 'chem_1_b1_s3'
    into a case-insensitive SQL LIKE pattern that matches canonical IDs.
    Examples:
      chem_b1_b1   -> 'chem_%_b1_b1'      (missing main chem number -> wildcard)
      chem_1_b1    -> 'chem_1_b1'
      chem_1_b1_s3 -> 'chem_1_b1_s3'
    """
    if not q:
        return None
    s = re.sub(r'[^a-z0-9]+', '_', q.strip().lower()).strip('_')
    m = re.match(r'^chem(?:_(\d+))?_b(\d+)(?:_([bsh])(\d+))?$', s)
    if not m:
        return None
    chemno, bottleno, kind, batchno = m.groups()
    like = f"chem_{chemno or '%'}_b{bottleno}"
    if kind and batchno:
        like += f"_{kind}{batchno}"
    return like

# -------- Helpers --------
def _resolve_chemical_data(val):
    val = val.strip()
    if not val: return None
    
    # 1. Detect type
    cas_re = re.compile(r"^\d{2,7}-\d{2}-\d$")
    info = {}
    
    if cas_re.match(val):
        info = cas_client.lookup_by_cas(val, full=True, mf_mw=True)
    else:
        # Check if SMILES
        is_smiles = False
        try:
            m = Chem.MolFromSmiles(val)
            if m: is_smiles = True
        except:
            pass
            
        if is_smiles:
             info = cas_client.lookup_by_smiles(val, full=True, mf_mw=True)
             if not info: info = {"smiles": val}
        else:
             # Try IUPAC/Name -> SMILES
             s = iupac_to_kekule_smiles(val)
             if s:
                 info = cas_client.lookup_by_smiles(s, full=True, mf_mw=True)
                 if not info: info = {"smiles": s, "name": val}
             else:
                 # Try Name directly
                 info = cas_client.lookup_by_name(val, full=True, mf_mw=True)

    if not info: return None

    # 2. RDKit calculations
    final_smiles = info.get("smiles")
    if final_smiles:
        try:
            mol = Chem.MolFromSmiles(final_smiles)
            if mol:
                if not info.get("molecular_weight"):
                    info["molecular_weight"] = Descriptors.MolWt(mol)
                if not info.get("molecular_formula"):
                    info["molecular_formula"] = rdMolDescriptors.CalcMolFormula(mol)
                info["mim"] = Descriptors.ExactMolWt(mol)
                if not info.get("inchi"):
                    info["inchi"] = Chem.MolToInchi(mol)
                if not info.get("inchikey"):
                    info["inchikey"] = Chem.MolToInchiKey(mol)
        except:
            pass
            
    return {
        "common_name_abb": info.get("name") or val,
        "cas": info.get("cas"),
        "chemform": info.get("molecular_formula"),
        "mw": info.get("molecular_weight"),
        "mim": info.get("mim"),
        "smiles": info.get("smiles"),
        "inchi": info.get("inchi"),
        "inchi_key": info.get("inchikey"),
    }

# -------- Routes --------
@app.route('/')
def home():
    return render_template('index.html')




@app.get("/health")
def health():
    try:
        with engine.connect() as conn:
            conn.execute(sq_text("SELECT 1"))
        return {"status": "ok"}
    except Exception as e:
        return {"status": "error", "detail": str(e)}, 500

@app.get("/jump")
def jump():
    q = (request.args.get("q") or "").strip()
    if not q:
        return redirect(url_for("home"))

    session = SessionLocal()
    try:
        # Direct ID patterns first
        low = q.lower()
        if re.fullmatch(r"chem_\d+", q, flags=re.I):
            if session.query(Chemical).filter(func.lower(Chemical.chem_id)==low).first():
                return redirect(url_for("chemicals", q=q))

        if re.fullmatch(r"chem_\d+_b\d+$", q, flags=re.I):
            if session.query(Bottle).filter(func.lower(Bottle.bottle_id)==low).first():
                return redirect(url_for("bottles", q=q))

        if re.fullmatch(r"chem_\d+_b\d+_[bsh]\d+$", q, flags=re.I):
            if session.query(Batch).filter(func.lower(Batch.batch_id)==low).first():
                return redirect(url_for("batches", q=q))

        # Fallback: look across entities
        like = f"%{low}%"

        c = (session.query(Chemical).filter(or_(
            func.lower(Chemical.chem_id).like(like),
            func.lower(Chemical.common_name_abb).like(like),
            func.lower(Chemical.cas).like(like),
            func.lower(Chemical.smiles).like(like),
            func.lower(Chemical.inchi_key).like(like),
        )).first())
        if c:
            return redirect(url_for("chemicals", q=q))

        b = (session.query(Bottle).filter(or_(
            func.lower(Bottle.bottle_id).like(like),
            func.lower(Bottle.chem_id).like(like),
            func.lower(Bottle.supplier_id).like(like),
            func.lower(Bottle.lot_no).like(like),
            func.lower(Bottle.barcode).like(like),
        )).first())
        if b:
            return redirect(url_for("bottles", q=q))

        bt = (session.query(Batch).filter(or_(
            func.lower(Batch.batch_id).like(like),
            func.lower(Batch.chem_id).like(like),
            func.lower(Batch.barcode).like(like),
            func.lower(Batch.location).like(like),
            func.lower(Batch.sublocation).like(like),
            func.lower(Batch.status).like(like),
        )).first())
        if bt:
            return redirect(url_for("batches", q=q))

        # default: show chemicals with the query
        return redirect(url_for("chemicals", q=q))
    finally:
        session.close()

@app.get("/search")
def unified_search():
    session = SessionLocal()
    q = (request.args.get("q") or "").strip()
    low = q.strip().lower()
    # If it's exactly a Chem_ID, return only that chemical and its bottles/batches
    if re.fullmatch(r'chem_\d+', low):
        chems = session.query(Chemical).filter(func.lower(Chemical.chem_id) == low).all()
        bottles = (session.query(Bottle)
                .filter(func.lower(Bottle.chem_id) == low)
                .order_by(Bottle.bottle_id.asc())
                .limit(100).all())
        batches = (session.query(Batch)
                .filter(func.lower(Batch.chem_id) == low)
                .order_by(Batch.created_at.desc())
                .limit(200).all())
        return render_template("search.html", query=q, chems=chems, bottles=bottles, batches=batches)
    try:
        if not q:
            return render_template("search.html", query=q, chems=[], bottles=[], batches=[])

        like_any = f"%{q}%"
        id_like = _build_id_like(q)  # tolerant ID pattern, e.g. chem_%_b1_b1

        # ---- Chemicals: case-insensitive across common fields ----
        chems = (
            session.query(Chemical)
            .filter(or_(
                Chemical.chem_id.ilike(like_any),
                Chemical.common_name_abb.ilike(like_any),
                Chemical.cas.ilike(like_any),
                Chemical.smiles.ilike(like_any),
                Chemical.inchi_key.ilike(like_any),
                Chemical.ro_srn.ilike(like_any),
                Chemical.chemform.ilike(like_any),
            ))
            .order_by(Chemical.chem_id.asc())
            .limit(100)
            .all()
        )

        # ---- Bottles: normal text ilike + tolerant ID pattern on bottle_id ----
        qb = session.query(Bottle)
        conds_b = [
            Bottle.bottle_id.ilike(like_any),
            Bottle.chem_id.ilike(like_any),
            Bottle.supplier_id.ilike(like_any),
            Bottle.lot_no.ilike(like_any),
            getattr(Bottle, "size_amount").ilike(like_any) if hasattr(Bottle, "size_amount") else text("1=0"),
        ]
        if hasattr(Bottle, "barcode"):
            conds_b.append(Bottle.barcode.ilike(like_any))
        if id_like:
            conds_b.append(Bottle.bottle_id.ilike(id_like))

        bottles = (
            qb.filter(or_(*conds_b))
              .order_by(Bottle.bottle_id.asc())
              .limit(100)
              .all()
        )

        # ---- Batches: normal text ilike + tolerant ID pattern on batch_id ----
        qbt = session.query(Batch)
        conds_bt = [
            Batch.batch_id.ilike(like_any),
            Batch.chem_id.ilike(like_any),
            Batch.location.ilike(like_any),
            Batch.sublocation.ilike(like_any),
            Batch.status.ilike(like_any),
            getattr(Batch, "kind").ilike(like_any) if hasattr(Batch, "kind") else text("1=0"),
        ]
        if hasattr(Batch, "barcode"):
            conds_bt.append(Batch.barcode.ilike(like_any))
        if id_like:
            conds_bt.append(Batch.batch_id.ilike(id_like))

        batches = (
            qbt.filter(or_(*conds_bt))
               .order_by(Batch.created_at.desc())
               .limit(200)
               .all()
        )

        return render_template(
            "search.html",
            query=q,
            chems=chems,
            bottles=bottles,
            batches=batches,
        )
    except Exception as e:
        current_app.logger.exception("unified_search failed")
        # Friendly fallback instead of 500
        return render_template(
            "search.html", query=q, chems=[], bottles=[], batches=[], error=str(e)
        ), 200
    finally:
        session.close()

# ---- Chemical: read one (JSON for editor) ----
@app.get("/chemicals/<chem_id>/json")
def chem_one_json(chem_id):
    session = SessionLocal()
    try:
        c = session.query(Chemical).filter(Chemical.chem_id == chem_id).first()
        if not c:
            return jsonify({"ok": False, "error": "Not found"}), 404
        item = {
            "chem_id": c.chem_id,
            "common_name_abb": c.common_name_abb,
            "cas": c.cas,
            "ro_srn": c.ro_srn,
            "chemform": c.chemform,
            "mw": c.mw,
            "mim": c.mim,
            "density": c.density,
            "aggregate_state": c.aggregate_state,
            "stock_solution_c": c.stock_solution_c,
            "smiles": c.smiles,
            "inchi": c.inchi,
            "inchi_key": c.inchi_key,
        }
        return jsonify({"ok": True, "item": item})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500
    finally:
        session.close()


# ---- Chemical: update (save from editor) ----
@app.post("/chemicals/<chem_id>/update")
def chem_update(chem_id):
    session = SessionLocal()
    data = request.get_json(silent=True) or {}
    try:
        c = session.query(Chemical).filter(Chemical.chem_id == chem_id).first()
        if not c:
            return jsonify({"ok": False, "error": "Not found"}), 404

        # Editable fields (chem_id stays immutable)
        c.common_name_abb = (data.get("common_name_abb") or "").strip() or None
        c.cas             = (data.get("cas") or "").strip() or None
        c.ro_srn          = (data.get("ro_srn") or "").strip() or None
        c.chemform        = (data.get("chemform") or "").strip() or None
        c.mw              = parse_float(data.get("mw"))
        c.mim             = (data.get("mim") or "").strip() or None
        c.density         = parse_float(data.get("density"))
        c.aggregate_state = (data.get("aggregate_state") or "").strip() or None
        c.stock_solution_c= (data.get("stock_solution_c") or "").strip() or None
        c.smiles          = (data.get("smiles") or "").strip() or None
        c.inchi           = (data.get("inchi") or "").strip() or None
        c.inchi           = (data.get("inchi") or "").strip() or None
        c.inchi_key       = (data.get("inchi_key") or "").strip() or None

        # Compute properties if SMILES changed or not present
        if c.smiles:
            c.functional_groups = compute_functional_groups(c.smiles)
            c.structure_svg = generate_structure_svg(c.smiles)

        session.commit()
        return jsonify({"ok": True})
    except Exception as e:
        session.rollback()
        return jsonify({"ok": False, "error": str(e)}), 500
    finally:
        session.close()

# ---- Chemicals DB ----
@app.get("/chemicals")
def chemicals():
    session = SessionLocal()
    num_col = cast(func.regexp_replace(Chemical.chem_id, r'^Chem_', ''), Integer)
    q = session.query(Chemical).order_by(num_col.desc(), Chemical.chem_id.desc())
    
    search = request.args.get("q", "").strip()
    fg_filter = request.args.getlist("fg") # Functional group filter

    if fg_filter:
        # Filter by functional groups (OR logic by default as per requirements)
        # We use JSON containment operator @> or similar if supported, but for SQLite/Generic we might need text matching
        # Since functional_groups is JSON, we can use JSON operators if using Postgres
        # Assuming Postgres based on psycopg2 dependency
        
        # Postgres JSONB containment: column @> '["Group"]'
        # But here we want ANY of the selected groups.
        # So we construct OR conditions: functional_groups @> '["Group1"]' OR functional_groups @> '["Group2"]'
        
        fg_conditions = []
        for fg in fg_filter:
            # Construct JSON string for containment
            # Using cast to JSONB if needed, but let's try generic text search as fallback or specific JSON op
            # SQLAlchemy with Postgres: Chemical.functional_groups.contains([fg])
            # But let's check if the column is defined as JSON type in models.py (it is)
            
            # Using text-based search on JSON string as a safe fallback if JSON operators are tricky
            # The JSON is a list of strings: ["Alcohol", "Amine"]
            # We can search for '"Alcohol"'
            fg_conditions.append(cast(Chemical.functional_groups, String).like(f'%"{fg}"%'))
            
        if fg_conditions:
            q = q.filter(or_(*fg_conditions))

    if search:
        s_low = search.lower()
        # If the query is exactly a Chem_ID, do an exact match
        if re.fullmatch(r'chem_\d+', s_low):
            q = q.filter(func.lower(Chemical.chem_id) == s_low)
        else:
            like = f"%{s_low}%"
            q = q.filter(or_(
                func.lower(Chemical.chem_id).like(like),
                func.lower(Chemical.common_name_abb).like(like),
                func.lower(Chemical.cas).like(like),
                func.lower(Chemical.ro_srn).like(like),
                func.lower(Chemical.chemform).like(like),
                cast(Chemical.mw, String).like(like),
                func.lower(Chemical.mim).like(like),
                cast(Chemical.density, String).like(like),
                func.lower(Chemical.aggregate_state).like(like),
                func.lower(Chemical.stock_solution_c).like(like),
                cast(Chemical.purity, String).like(like),
                func.lower(Chemical.smiles).like(like),
                func.lower(Chemical.inchi).like(like),
                func.lower(Chemical.inchi_key).like(like),
            ))
    chems = q.all()
    return render_template("chemicals.html", chemicals=chems, search=search)

@app.post("/chemicals/create")
def create_chemical():
    session = SessionLocal()
    data = request.json or {}
    force = data.get("force", False)
    
    if not (data.get("common_name_abb")):
        session.close()
        return jsonify({"ok": False, "error": "Missing field: common_name_abb"}), 400

    # Helper to safely get string
    def safe_str(k):
        v = data.get(k)
        if v is None: return None
        return str(v).strip() or None

    try:
        # 1. Duplicate Check (unless forced)
        if not force:
            start_smiles = safe_str("smiles")
            start_inchi = safe_str("inchi")
            start_cas = safe_str("cas")
            
            # Build query for potential duplicates
            # Match ANY of: SMILES, InChI, or CAS (if provided)
            conditions = []
            if start_smiles:
                # Compare loose or exact? Let's use exact for now, or maybe chemical equivalence?
                # For strict duplicate check: exact match on SMILES string (assuming canonicalized by RDKit if possible, but here just string)
                # Ideally we should canonicalize incoming SMILES if not already done.
                # data['smiles'] often comes from RDKit resolution in frontend or backend helper.
                conditions.append(Chemical.smiles == start_smiles)
            if start_inchi:
                conditions.append(Chemical.inchi == start_inchi)
            if start_cas:
                conditions.append(Chemical.cas == start_cas)
                
            if conditions:
                existing = session.query(Chemical).filter(or_(*conditions)).first()
                if existing:
                    # Found a duplicate!
                    return jsonify({
                        "ok": False, 
                        "error": "Duplicate found", 
                        "duplicate": True,
                        "existing": existing.to_dict()
                    }), 409

        # 2. PROCEED TO CREATE
        chem = Chemical(
            chem_id=next_chem_id(session),
            common_name_abb=safe_str("common_name_abb") or "",
            cas=safe_str("cas"),
            ro_srn=safe_str("ro_srn"),
            chemform=safe_str("chemform"),
            mw=parse_float(data.get("mw")),
            mim=safe_str("mim"),
            density=parse_float(data.get("density")),
            aggregate_state=safe_str("aggregate_state"),
            stock_solution_c=safe_str("stock_solution_c"),
            smiles=safe_str("smiles"),
            inchi=safe_str("inchi"),
            inchi_key=safe_str("inchi_key"),
            functional_groups=compute_functional_groups(safe_str("smiles")),
            structure_svg=generate_structure_svg(safe_str("smiles"))
        )
        session.add(chem)
        session.commit()
        return jsonify({"ok": True, "chem": chem.to_dict()})
    except Exception as e:
        session.rollback()
        current_app.logger.exception("create_chemical failed")
        return jsonify({"ok": False, "error": str(e)}), 500
    finally:
        session.close()



@app.post("/chemicals/bulk_preview")
def bulk_preview():
    session = SessionLocal()
    data = request.json or {}
    lines = data.get("lines") or []
    
    results = []
    try:
        for line in lines:
            val = line.strip()
            if not val: continue
            
            # 1. Resolve Data
            item = _resolve_chemical_data(val)
            if not item:
                results.append({"input": val, "status": "error", "error": "Could not resolve identifier"})
                continue
                
            # 2. Check Duplicates
            # Check SMILES, CAS, InChI
            conds = []
            if item.get("smiles"): conds.append(Chemical.smiles == item["smiles"])
            if item.get("inchi"): conds.append(Chemical.inchi == item["inchi"])
            if item.get("cas"): conds.append(Chemical.cas == item["cas"])
            
            existing = None
            if conds:
                existing = session.query(Chemical).filter(or_(*conds)).first()
            
            if existing:
                results.append({
                    "input": val, 
                    "status": "conflict", 
                    "item": item, 
                    "existing": {
                        "chem_id": existing.chem_id,
                        "common_name_abb": existing.common_name_abb,
                        "cas": existing.cas
                    }
                })
            else:
                results.append({
                    "input": val,
                    "status": "valid",
                    "item": item
                })
                
        return jsonify({"ok": True, "results": results})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500
    finally:
        session.close()

@app.post("/chemicals/bulk_create_confirm")
def bulk_create_confirm():
    session = SessionLocal()
    data = request.json or {}
    items = data.get("items") or [] # List of full item dicts to create
    
    created_count = 0
    errors = []
    
    try:
        for item in items:
            try:
                # We assume these items are already resolved and user confirmed them (even if duplicate)
                chem_id = next_chem_id(session)
                chem = Chemical(
                    chem_id=chem_id,
                    common_name_abb=item.get("common_name_abb","").strip(),
                    cas=item.get("cas"),
                    chemform=item.get("chemform"),
                    mw=item.get("mw"),
                    mim=str(item.get("mim")) if item.get("mim") else None,
                    smiles=item.get("smiles"),
                    inchi=item.get("inchi"),
                    inchi_key=item.get("inchi_key"),
                    functional_groups=compute_functional_groups(item.get("smiles")),
                    structure_svg=generate_structure_svg(item.get("smiles")),
                    ro_srn=None, density=None, aggregate_state=None, stock_solution_c=None
                )
                session.add(chem)
                session.commit() # Commit each to ensure ID increment works if next_chem_id reads DB
                created_count += 1
            except Exception as ex:
                session.rollback()
                errors.append(f"Failed to create {item.get('common_name_abb')}: {ex}")
        
        return jsonify({"ok": True, "count": created_count, "errors": errors})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500
    finally:
        session.close()

@app.get("/chemicals/<chem_id>/availability")
def chem_availability(chem_id):
    session = SessionLocal()
    try:
        # Mark expired for THIS chem_id only (if an expiring_date is in the past)
        today = date.today()
        session.query(Batch).filter(
            Batch.chem_id == chem_id,
            Batch.expiring_date != None,
            Batch.expiring_date <= today,
            Batch.status != "Expired",
        ).update({Batch.status: "Expired"}, synchronize_session=False)
        session.commit()

        # Return ALL batches (Availability check logic remains for expired status update, but we show everything)
        batches = (
            session.query(Batch)
            .filter(Batch.chem_id == chem_id)
            .order_by(Batch.batch_id.asc())
            .all()
        )

        out = [
            {"batch_id": b.batch_id, "location": b.location, "sublocation": b.sublocation or ""}
            for b in batches
        ]
        return jsonify({"ok": True, "items": out})
    except Exception as e:
        session.rollback()
        return jsonify({"ok": False, "error": str(e)}), 500
    finally:
        session.close()

@app.get("/chemicals/lookup")
def chem_lookup():
    session = SessionLocal()
    q = (request.args.get("q") or "").strip().lower()
    if not q:
        rows = session.query(Chemical).order_by(Chemical.chem_id.asc()).limit(50).all()
    else:
        like = f"%{q}%"
        rows = (session.query(Chemical)
                .filter(or_(func.lower(Chemical.chem_id).like(like),
                            func.lower(Chemical.common_name_abb).like(like),
                            func.lower(Chemical.cas).like(like),
                            func.lower(Chemical.smiles).like(like),
                            func.lower(Chemical.inchi_key).like(like)))
                .order_by(Chemical.chem_id.asc()).limit(50).all())
    return jsonify({"ok": True, "items": [
        {"chem_id": c.chem_id, "label": f"{c.chem_id} · {c.common_name_abb or ''} · {c.cas or ''}".strip()} for c in rows
    ]})

# ---- Suppliers ----
@app.get("/suppliers")
def suppliers_list():
    session = SessionLocal()
    q = (request.args.get("q") or "").strip().lower()
    base = session.query(Supplier).order_by(Supplier.name.asc())
    if q:
        like = f"%{q}%"
        base = base.filter(func.lower(Supplier.name).like(like))
    names = [s.name for s in base.all()]
    return jsonify({"ok": True, "suppliers": names})

from utils.chem_utils import compute_functional_groups, generate_structure_svg, generate_pdf_export, FUNCTIONAL_GROUPS, FG_METADATA

# ---- ChemDB Enhancements ----
@app.get("/chemicals/functional_groups")
def get_functional_groups():
    # Return list of {id, label, category}
    groups = []
    for key in sorted(FUNCTIONAL_GROUPS.keys()):
        groups.append({
            "id": key,
            "label": key.replace("_", " ").title(), # Prettify label
            "category": FG_METADATA.get(key, "Other")
        })
    return jsonify({"ok": True, "groups": groups})

@app.get("/chemicals/<chem_id>/preview")
def get_chem_preview(chem_id):
    session = SessionLocal()
    try:
        c = session.query(Chemical).filter(Chemical.chem_id == chem_id).first()
        if not c:
            return "Not found", 404
        
        # If not cached, compute it
        if not c.structure_svg and c.smiles:
            c.structure_svg = generate_structure_svg(c.smiles)
            session.commit()
            
        if not c.structure_svg:
            return "No structure available", 404
            
        return c.structure_svg, 200, {'Content-Type': 'image/svg+xml'}
    finally:
        session.close()

@app.post("/chemicals/export")
def export_chemicals():
    data = request.json or {}
    chem_ids = data.get("chem_ids") or []
    
    if not chem_ids:
        return jsonify({"ok": False, "error": "No chemicals selected"}), 400
        
    session = SessionLocal()
    try:
        chems = session.query(Chemical).filter(Chemical.chem_id.in_(chem_ids)).all()
        
        # Prepare data for export
        export_data = []
        # Maintain order of selection if possible, or just sort by ID
        # Here we just map the results
        chem_map = {c.chem_id: c for c in chems}
        
        for cid in chem_ids:
            if cid in chem_map:
                c = chem_map[cid]
                export_data.append({
                    "Chem_ID": c.chem_id,
                    "SMILES": c.smiles,
                    "CAS": c.cas,
                    "RO SRN": c.ro_srn
                })
                
        pdf_bytes = generate_pdf_export(export_data)
        
        # Return as downloadable file
        from flask import make_response
        response = make_response(pdf_bytes)
        response.headers['Content-Type'] = 'application/pdf'
        response.headers['Content-Disposition'] = 'attachment; filename=chemicals_export.pdf'
        return response
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500
    finally:
        session.close()

@app.get("/sd_exporter")
def sd_exporter():
    session = SessionLocal()
    try:
        num_col = cast(func.regexp_replace(Chemical.chem_id, r'^Chem_', ''), Integer)
        q = session.query(Chemical).order_by(num_col.desc(), Chemical.chem_id.desc())
        
        search = request.args.get("q", "").strip()
        fg_filter = request.args.getlist("fg")
        mode = request.args.get("mode", "any")

        if fg_filter:
            fg_conditions = []
            for fg in fg_filter:
                fg_conditions.append(cast(Chemical.functional_groups, String).like(f'%"{fg}"%'))
            
            if mode == 'all':
                for cond in fg_conditions:
                    q = q.filter(cond)
            else:
                q = q.filter(or_(*fg_conditions))

        if search:
            s_low = search.lower()
            if re.fullmatch(r'chem_\d+', s_low):
                q = q.filter(func.lower(Chemical.chem_id) == s_low)
            else:
                like = f"%{s_low}%"
                q = q.filter(or_(
                    func.lower(Chemical.chem_id).like(like),
                    func.lower(Chemical.common_name_abb).like(like),
                    func.lower(Chemical.cas).like(like),
                    func.lower(Chemical.ro_srn).like(like),
                    func.lower(Chemical.chemform).like(like),
                    func.lower(Chemical.smiles).like(like),
                    func.lower(Chemical.inchi_key).like(like),
                ))
        chems = q.all()
        return render_template("sd_exporter.html", chemicals=chems, search=search)
    finally:
        session.close()

# ---- Bottles DB ----
@app.get("/bottles")
def bottles():
    session = SessionLocal()
    qstr = (request.args.get("q") or "").strip()
    try:
        q = session.query(Bottle).order_by(Bottle.bottle_id.asc())

        if qstr:
            like = f"%{qstr.lower()}%"
            conds = [
                func.lower(Bottle.bottle_id).like(like),
                func.lower(Bottle.chem_id).like(like),
                func.lower(Bottle.supplier_id).like(like),
                func.lower(Bottle.lot_no).like(like),
                func.lower(Bottle.size_amount).like(like),
            ]
            if hasattr(Bottle, "barcode"):
                conds.append(func.lower(Bottle.barcode).like(like))
            q = q.filter(or_(*conds))

        rows = q.all()

        # If it's a search and no rows, show the unified search UI (search.html)
        if qstr and not rows:
            return redirect(url_for("unified_search", q=qstr))

        return render_template("bottles.html", bottles=rows, search=qstr)

    except Exception:
        current_app.logger.exception("bottles() failed")
        # Fall back to unified search UI so user sees a friendly page
        if qstr:
            return redirect(url_for("unified_search", q=qstr))
        return render_template(
            "bottles.html", bottles=[], search=qstr,
            error="Could not load bottles (see server logs)."
        ), 200
    finally:
        session.close()

@app.post("/bottles/create/<chem_id>")
def create_bottle(chem_id):
    session = SessionLocal()
    data = request.get_json(silent=True) or {}
    try:
        supplier_id = (data.get("supplier_id") or "").strip()
        lot_no      = (data.get("Lot_no") or data.get("lot_no") or "").strip()
        purity      = (data.get("purity") or "").strip()
        size_amount = (data.get("size_amount") or "").strip()
        # Accept both "barcode" and "Barcode"
        barcode     = (data.get("barcode") or data.get("Barcode") or "").strip()

        if not supplier_id or not lot_no or not purity or not size_amount:
            return jsonify(ok=False, error="Missing required fields"), 400
        if hasattr(Bottle, "barcode") and not barcode:
            return jsonify(ok=False, error="Missing Barcode"), 400

        # Compute next bottle number/id
        rows = session.query(Bottle).filter(Bottle.chem_id == chem_id).all()
        max_b = 0
        for r in rows:
            try:
                suffix = str(r.bottle_id).rsplit("_B", 1)[1]
                max_b = max(max_b, int(suffix))
            except Exception:
                pass
        bottle_no = max_b + 1
        bottle_id = f"{chem_id}_B{bottle_no}"

        b = Bottle(
            bottle_id=bottle_id,
            chem_id=chem_id,
            supplier_id=supplier_id,
            lot_no=lot_no,
            purity=purity,
            size_amount=size_amount,
        )
        if hasattr(Bottle, "barcode"):
            setattr(b, "barcode", barcode)

        session.add(b)
        session.commit()
        return jsonify(ok=True, bottle_id=bottle_id, bottle_no=bottle_no)
    except Exception:
        session.rollback()
        current_app.logger.exception("create_bottle failed")
        return jsonify(ok=False, error="Failed to create bottle"), 500
    finally:
        session.close()

# ---- Bottles DB ----
# ---- Bottles DB ----

@app.get("/bottles/bulk_template")
def bottles_bulk_template():
    try:
        # Create a simple Excel file in memory
        df = pd.DataFrame(columns=[
            "Chemical Identifier", "Supplier", "Lot No", "Purity", "Amount", 
            "Barcode", "Location", "Sublocation"
        ])
        # Add a dummy example row to help user
        df.loc[0] = ["Chem_1", "Sigma", "L12345", 0.98, "5g", "", "Main Lab", "Shelf A"]
        
        from io import BytesIO
        output = BytesIO()
        with pd.ExcelWriter(output, engine='openpyxl') as writer:
            df.to_excel(writer, index=False, sheet_name='Template')
        output.seek(0)
        
        from flask import send_file
        return send_file(output, download_name="bottle_import_template.xlsx", as_attachment=True)
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500

@app.post("/bottles/bulk_import")
def bottles_bulk_preview():
    """
    PREVIEW ONLY - Does not save to DB.
    Parses Excel, resolves chemicals, and proposes bottle/batch IDs.
    """
    session = SessionLocal()
    try:
        if 'file' not in request.files:
            return jsonify({"ok": False, "error": "No file part"}), 400
        
        file = request.files['file']
        if file.filename == '':
            return jsonify({"ok": False, "error": "No selected file"}), 400

        if not file.filename.endswith(('.xlsx', '.xls')):
            return jsonify({"ok": False, "error": "Invalid file type. Please upload Excel."}), 400

        try:
            df = pd.read_excel(file)
        except Exception as e:
            return jsonify({"ok": False, "error": f"Failed to parse Excel: {str(e)}"}), 400

        def clean(val):
            if pd.isna(val): return None
            return str(val).strip()
            
        def clean_float(val):
            if pd.isna(val): return None
            try: return float(val)
            except: return None

        preview_bottles = []
        preview_batches = []
        errors = []
        
        # We need to track proposed IDs to avoid conflicts within the same upload batch
        # Map chem_id -> current max bottle suffix
        chem_suffix_map = {} 

        for idx, row in df.iterrows():
            row_num = idx + 2
            
            chem_input = clean(row.get("Chemical Identifier"))
            if not chem_input: continue

            # 1. Resolve Chemical
            chem = resolve_chem_identifier(session, chem_input)
            if not chem:
                if re.fullmatch(r'\d+', chem_input):
                    chem = resolve_chem_identifier(session, f"Chem_{chem_input}")
            
            if not chem:
                errors.append(f"Row {row_num}: Could not resolve chemical '{chem_input}'")
                continue

            # 2. Determine Next Bottle ID (considering what we've already allocated in this loop)
            if chem.chem_id not in chem_suffix_map:
                chem_suffix_map[chem.chem_id] = next_bottle_suffix(session, chem.chem_id)
            else:
                chem_suffix_map[chem.chem_id] += 1
            
            bottle_no = chem_suffix_map[chem.chem_id]
            bottle_id = f"{chem.chem_id}_B{bottle_no}"
            
            # 3. Prepare Bottle Data
            supplier = clean(row.get("Supplier")) or "Unknown"
            lot_no = clean(row.get("Lot No")) or "Unknown"
            purity = clean_float(row.get("Purity")) or 0.0
            amount = clean(row.get("Amount")) or "0"
            barcode = clean(row.get("Barcode"))
            location = clean(row.get("Location")) or "Main Lab"
            sublocation = clean(row.get("Sublocation"))

            # Bottle Item
            b_item = {
                "row_num": row_num,
                "bottle_id": bottle_id, # System generated
                "chem_id": chem.chem_id,
                "supplier_id": supplier,
                "lot_no": lot_no,
                "purity": purity,
                "amount": amount,
                "barcode": barcode or ""
            }
            preview_bottles.append(b_item)

            # 4. Prepare Batch Data (Auto-generated from Bottle)
            # Standard: Chem_X_BX_B1
            batch_id = f"{bottle_id}_B1"
            
            bt_item = {
                "batch_id": batch_id, # System generated
                "chem_id": chem.chem_id,
                "kind": "Bottle",
                "linked_bottle_id": bottle_id,
                "location": location,
                "sublocation": sublocation or "",
                "status": "Available",
                "amount": amount,
                "barcode": barcode or batch_id # Use bottle barcode if exist
            }
            preview_batches.append(bt_item)

        return jsonify({
            "ok": True, 
            "preview": {
                "bottles": preview_bottles,
                "batches": preview_batches
            },
            "errors": errors
        })

    except Exception as e:
        return jsonify({"ok": False, "error": f"Server Error: {str(e)}"}), 500
    finally:
        session.close()

@app.post("/bottles/bulk_commit")
def bottles_bulk_commit():
    """
    Final Commit of confirmed Bottle/Batch data.
    Receives JSON with 'bottles' and 'batches'.
    """
    session = SessionLocal()
    data = request.json or {}
    bottles_data = data.get("bottles", [])
    batches_data = data.get("batches", [])
    
    if not bottles_data:
        return jsonify({"ok": False, "error": "No data to commit"}), 400
        
    created_count = 0
    errors = []
    
    try:
        # 1. Commit Bottles
        for b_item in bottles_data:
            try:
                # Validation
                if not b_item.get("chem_id") or not b_item.get("bottle_id"):
                    continue

                new_bottle = Bottle(
                    bottle_id=b_item["bottle_id"],
                    chem_id=b_item["chem_id"],
                    supplier_id=b_item.get("supplier_id") or "Unknown",
                    lot_no=b_item.get("lot_no") or "Unknown",
                    purity=b_item.get("purity") or 0.0,
                    size_amount=b_item.get("amount") or "0",
                    barcode=b_item.get("barcode")
                )
                session.add(new_bottle)
                created_count += 1
            except Exception as e:
                errors.append(f"Failed to create bottle {b_item.get('bottle_id')}: {str(e)}")
        
        # 2. Commit Batches
        for bt_item in batches_data:
            try:
                # Re-derive sorting/index details from ID string to be safe
                # batch_id ex: Chem_1_B2_B1
                # kind_index = 1
                # bottle_no = 2
                
                # Simplified parsing assuming correct format from preview
                batch_id = bt_item["batch_id"]
                parts = batch_id.split("_")
                # Chem_1_B2_B1 -> parts: Chem, 1, B2, B1
                
                bottle_part = parts[-2] # B2
                bottle_no = int(bottle_part.replace("B", ""))
                
                kind_part = parts[-1] # B1
                kind_index = int(kind_part.replace("B", ""))

                new_batch = Batch(
                    batch_id=batch_id,
                    chem_id=bt_item["chem_id"],
                    kind="Bottle",
                    bottle_no=bottle_no,
                    kind_index=kind_index,
                    concentration_moll=None,
                    barcode=bt_item.get("barcode"),
                    location=bt_item.get("location"),
                    sublocation=bt_item.get("sublocation"),
                    amount=bt_item.get("amount"),
                    status="Available"
                )
                session.add(new_batch)
            except Exception as e:
                 errors.append(f"Failed to create batch {bt_item.get('batch_id')}: {str(e)}")

        session.commit()
        
        return jsonify({
            "ok": True, 
            "created": created_count,
            "errors": errors
        })
        
    except Exception as e:
        session.rollback()
        return jsonify({"ok": False, "error": str(e)}), 500
    finally:
        session.close()
@app.get("/batches")
def batches():
    session = SessionLocal()
    qstr = (request.args.get("q") or "").strip()
    try:
        q = session.query(Batch).order_by(Batch.created_at.desc())

        if qstr:
            like = f"%{qstr}%"
            q = q.filter(or_(
                Batch.batch_id.ilike(like),
                Batch.chem_id.ilike(like),
                Batch.location.ilike(like),
                Batch.sublocation.ilike(like),
                Batch.status.ilike(like),
                Batch.kind.ilike(like),
            ))

        rows = q.all()

        # Fetch QC results for these batches
        batch_ids = [r.batch_id for r in rows]
        qc_map = {}
        if batch_ids:
            qc_results = session.query(QCResult.batch_id).filter(QCResult.batch_id.in_(batch_ids)).all()
            qc_map = {r.batch_id: True for r in qc_results}

        # If it's a search and no rows, show the unified search UI (search.html)
        if qstr and not rows:
            return redirect(url_for("unified_search", q=qstr))

        return render_template("batches.html", batches=rows, search=qstr, qc_map=qc_map)

    except Exception:
        current_app.logger.exception("batches() failed")
        if qstr:
            return redirect(url_for("unified_search", q=qstr))
        return render_template(
            "batches.html", batches=[], search=qstr,
            error="Could not load batches (see server logs)."
        ), 200
    finally:
        session.close()

@app.post("/batches/bulk_update")
def batches_bulk_update():
    session = SessionLocal()
    data = request.json or {}
    batch_ids = data.get("batch_ids", [])
    
    # New flexible payload
    updates = data.get("updates", {})
    
    if not updates:
        action = data.get("action")
        if action == "location":
            updates = {
                "location": data.get("location"),
                "sublocation": data.get("sublocation")
            }
        elif action == "status":
            updates = {"status": data.get("status")}
        elif action == "amount":
            updates = {"amount": data.get("amount")}

    if not batch_ids:
        return jsonify({"ok": False, "error": "No batches selected"}), 400
    if not updates:
        return jsonify({"ok": False, "error": "No updates provided"}), 400
        
    try:
        allowed_fields = {"location", "sublocation", "status", "amount"}
        safe_updates = {k: v for k, v in updates.items() if k in allowed_fields}
        
        if not safe_updates:
            return jsonify({"ok": False, "error": "No valid fields to update"}), 400

        stmt = (
            update(Batch)
            .where(Batch.batch_id.in_(batch_ids))
            .values(safe_updates)
            .execution_options(synchronize_session=False)
        )
        result = session.execute(stmt)
        session.commit()
        
        return jsonify({"ok": True, "updated": result.rowcount})
    except Exception as e:
        session.rollback()
        return jsonify({"ok": False, "error": str(e)}), 500
    finally:
        session.close()

@app.post("/batches/create_for_new_bottle")
def batch_create_for_new_bottle():
    session = SessionLocal()
    data = request.json or {}
    required = ["chem_id","bottle_no","Barcode","location","amount"]
    for k in required:
        if not data.get(k):
            return jsonify({"ok": False, "error": f"Missing {k}"}), 400

    chem_id = data["chem_id"]
    bottle_no = int(data["bottle_no"])
    sublocation = (data.get("sublocation") or "").strip() or None

    batch_no = next_batch_suffix(session, chem_id, bottle_no, "B")
    batch_id = f"{chem_id}_B{bottle_no}_B{batch_no}"

    batch = Batch(
        batch_id=batch_id,
        chem_id=chem_id,
        kind="Bottle",
        bottle_no=bottle_no,
        kind_index=batch_no,
        concentration_moll=None,  # only for Stock solutions
        barcode=data["Barcode"].strip(),
        location=(data["location"] or "").strip(),
        sublocation=sublocation,
        amount=(data["amount"] or "").strip(),
        status="Available"
    )
    try:
        session.add(batch)
        session.commit()
        return jsonify({"ok": True, "batch_id": batch_id})
    except Exception as e:
        session.rollback()
        return jsonify({"ok": False, "error": str(e)}), 500
    finally:
        session.close()

@app.post("/batches/create")
def batch_create():
    session = SessionLocal()
    data = request.json or {}
    required = ["chem_id","bottle_no","Type","Barcode","location","amount"]
    for k in required:
        if not data.get(k):
            return jsonify({"ok": False, "error": f"Missing {k}"}), 400

    chem_id = data["chem_id"]
    bottle_no = int(data["bottle_no"])
    kind_map = {"Bottle":"B", "Stock solution":"S", "Head":"H"}
    if data["Type"] not in kind_map:
        return jsonify({"ok": False, "error": "Invalid Type"}), 400
    kind_letter = kind_map[data["Type"]]

    next_no = next_batch_suffix(session, chem_id, bottle_no, kind_letter)
    batch_id = f"{chem_id}_B{bottle_no}_{kind_letter}{next_no}"

    exp_date = None
    if data["Type"] == "Stock solution":
        try:
            if not data.get("expiring_date"): return jsonify({"ok": False, "error": "Missing expiring_date"}), 400
            exp_date = datetime.fromisoformat(data.get("expiring_date")).date()
        except Exception:
            return jsonify({"ok": False, "error": "Invalid expiring_date"}), 400
    
    conc = None 
    if data["Type"] == "Stock solution":
        conc = parse_float(data.get("concentration_moll"))
    batch = Batch(
        batch_id=batch_id,
        chem_id=chem_id,
        kind=data["Type"],
        bottle_no=bottle_no,
        kind_index=next_no,
        concentration_moll=conc,
        barcode=data["Barcode"].strip(),
        location=(data["location"] or "").strip(),
        sublocation=(data.get("sublocation") or "").strip() or None,
        amount=(data["amount"] or "").strip(),
        status="Available",
        expiring_date=exp_date
    )
    try:
        session.add(batch)
        session.commit()
        return jsonify({"ok": True, "batch_id": batch_id})
    except Exception as e:
        session.rollback()
        return jsonify({"ok": False, "error": str(e)}), 500
    finally:
        session.close()

@app.post("/batches/manage/<batch_id>")
def batch_manage(batch_id):
    session = SessionLocal()
    data = request.json or {}
    try:
        bt = session.query(Batch).filter(Batch.batch_id == batch_id).first()
        if not bt:
            return jsonify({"ok": False, "error": "Not found"}), 404

        loc    = (data.get("location") or "").strip()
        subloc = (data.get("sublocation") or "").strip() or None
        status = (data.get("status") or "").strip()

        # enforce valid statuses
        if status not in ["Available","Empty","Stock Room","Expired"]:
            return jsonify({"ok": False, "error": "Invalid status"}), 400

        # business rule: when moved to Stock Room -> clear sublocation
        if status == "Stock Room":
            loc = "Stock Room"
            subloc = None  # <- important: wipe sublocation on Stock Room

        # stock-solution date rule (only when becoming Available)
        exp_ddmmyyyy = (data.get("expiring_date_ddmmyyyy") or "").strip()
        if bt.kind == "Stock solution" and status == "Available":
            if not exp_ddmmyyyy:
                return jsonify({"ok": False, "error": "Missing expiring date (dd/mm/yyyy) for Stock solution"}), 400
            try:
                dd, mm, yy = [int(x) for x in exp_ddmmyyyy.split("/")]
                bt.expiring_date = date(yy, mm, dd)
            except Exception:
                return jsonify({"ok": False, "error": "Invalid expiring date format; expected dd/mm/yyyy"}), 400

        bt.location    = loc
        bt.sublocation = subloc
        bt.status      = status

        session.commit()
        return jsonify({"ok": True})
    except Exception as e:
        session.rollback()
        return jsonify({"ok": False, "error": str(e)}), 500
    finally:
        session.close()
# ---- Plates & SURF ----
@app.get("/plates")
def plates():
    session = SessionLocal()
    plates = session.query(Plate).order_by(Plate.created_at.desc()).all()
    return render_template("plates.html", plates=plates)

@app.get("/plates/new")
def plate_new():
    plate_type = request.args.get("type", "24")
    if plate_type not in ["24","96"]:
        plate_type = "24"
    return render_template("plate_designer.html", plate_type=plate_type, prefill_json=None)

@app.get("/plates/edit/<int:plate_id>")
def plate_edit(plate_id):
    session = SessionLocal()
    p = session.query(Plate).filter(Plate.id==plate_id).first()
    if not p:
        return redirect(url_for("plates"))
    assignments = {}
    for w in session.query(PlateWellReagent).filter_by(plate_id=p.id).all():
        assignments.setdefault(f"well:{w.well}", []).append({
            "ref": w.chem_id, "category": w.category, "amount": w.amount
        })
    mix_mode, mix_rpm = "", ""
    if p.mixing and "_" in p.mixing:
        mm, rpm = p.mixing.split("_", 1)
        mix_mode, mix_rpm = mm, rpm
    prefill = {
        "assignments": assignments,
        "meta": {
            "eln": p.eln_id, "atmosphere": p.atmosphere,
            "mix_mode": mix_mode, "mix_rpm": mix_rpm,
            "wavelength_nm": p.wavelength_nm,
            "scale_mol": p.scale_mol,
            "concentration_mol_l": p.concentration_mol_l
        }
    }
    return render_template("plate_designer.html", plate_type=p.plate_type, prefill_json=prefill)

@app.post("/plates/save")
def plate_save():
    session = SessionLocal()
    data = request.json or {}
    meta = data.get("meta", {})
    assignments = data.get("assignments", {})
    plate_type = data.get("plate_type","24")
    if plate_type not in ["24","96"]:
        plate_type = "24"

    eln_id = (meta.get("eln") or "").strip()
    if not eln_id.startswith("ELN") or "-" not in eln_id:
        return jsonify({"ok": False, "error": "ELN must look like ELNXXXXXX-XXX"}), 400

    atmosphere = (meta.get("atmosphere") or "").strip() or None
    mode = meta.get("mix_mode")
    rpm = meta.get("mix_rpm")
    mixing = None
    if mode in ["st","sh"] and rpm:
        mixing = f"{mode}_{str(rpm).strip()}"

    wavelength_nm = parse_float(meta.get("wavelength_nm"))
    scale_mol = parse_float(meta.get("scale_mol"))
    concentration_mol_l = parse_float(meta.get("concentration_mol_l"))

    prev_max = session.query(func.coalesce(func.max(Plate.plate_no), 0)).filter(Plate.eln_id == eln_id).scalar()
    plate_no = int(prev_max) + 1

    plate = Plate(
        eln_id=eln_id,
        plate_no=plate_no,
        plate_type=plate_type,
        atmosphere=atmosphere,
        mixing=mixing,
        wavelength_nm=wavelength_nm,
        scale_mol=scale_mol,
        concentration_mol_l=concentration_mol_l,
    )

    try:
        session.add(plate)
        session.flush()

        rows = ["A","B","C","D"] if plate_type=="24" else ["A","B","C","D","E","F","G","H"]
        cols = list(range(1,7)) if plate_type=="24" else list(range(1,13))

        def merge_lists(base, add):
            m = {(x.get("category"), (x.get("ref") or "").strip().lower()): x for x in (base or [])}
            for x in (add or []):
                m[(x.get("category"), (x.get("ref") or "").strip().lower())] = x
            return list(m.values())

        plate_level = assignments.get("plate", [])
        for r in rows:
            row_level = merge_lists(plate_level, assignments.get(f"row:{r}", []))
            for c in cols:
                col_level = merge_lists(row_level, assignments.get(f"col:{c}", []))
                final = merge_lists(col_level, assignments.get(f"well:{r}{c}", []))
                for ent in final:
                    ref = (ent.get("ref") or "").strip()
                    cat = (ent.get("category") or "").strip().lower()
                    amt = parse_float(ent.get("amount"))
                    if not ref or not cat:
                        continue
                    chem = resolve_chem_identifier(session, ref)
                    if not chem:
                        session.rollback()
                        return jsonify({"ok": False, "error": f"Unknown chemical reference: '{ref}'"}), 400
                    session.add(PlateWellReagent(
                        plate_id=plate.id, well=f"{r}{c}",
                        chem_id=chem.chem_id, category=cat, amount=amt
                    ))

        session.flush()

        rows_data = {}
        for pwr in session.query(PlateWellReagent).filter_by(plate_id=plate.id).all():
            rows_data.setdefault(pwr.well, []).append(pwr)

        def take(vals, cat, n):
            arr = [v for v in vals if v.category==cat]
            ids = [v.chem_id for v in arr[:n]]
            eqs = [v.amount for v in arr[:n]]
            while len(ids)<n: ids.append(None); eqs.append(None)
            return ids, eqs

        for r in rows:
            for c in cols:
                wl = f"{r}{c}"
                vals = rows_data.get(wl, [])
                sm_ids, sm_eqs = take(vals, "starting_material", 2)
                rg_ids, rg_eqs = take(vals, "reagent", 5)
                sv_ids, sv_fr  = take(vals, "solvent", 2)

                session.add(SurfRow(
                    eln_id=plate.eln_id, plate_no=plate.plate_no, well=wl,
                    mixing=plate.mixing, atmosphere=plate.atmosphere, wavelength_nm=plate.wavelength_nm,
                    scale_mol=plate.scale_mol, concentration_mol_l=plate.concentration_mol_l,
                    startingmat_1_id=sm_ids[0], startingmat_1_eq=sm_eqs[0],
                    startingmat_2_id=sm_ids[1], startingmat_2_eq=sm_eqs[1],
                    reagent_1_id=rg_ids[0], reagent_1_eq=rg_eqs[0],
                    reagent_2_id=rg_ids[1], reagent_2_eq=rg_eqs[1],
                    reagent_3_id=rg_ids[2], reagent_3_eq=rg_eqs[2],
                    reagent_4_id=rg_ids[3], reagent_4_eq=rg_eqs[3],
                    reagent_5_id=rg_ids[4], reagent_5_eq=rg_eqs[4],
                    solvent_1_id=sv_ids[0], solvent_1_fraction=sv_fr[0],
                    solvent_2_id=sv_ids[1], solvent_2_fraction=sv_fr[1],
                    plate_id=plate.id
                ))

        session.commit()
        return jsonify({"ok": True, "plate_id": plate.id, "plate_no": plate.plate_no})
    except Exception as e:
        session.rollback()
        return jsonify({"ok": False, "error": str(e)}), 500
    finally:
        session.close()

@app.get("/surf")
def surf_table():
    session = SessionLocal()
    rows = session.query(SurfRow).order_by(SurfRow.id.asc()).all()
    return render_template("surf.html", surf=rows)


@app.get("/requests")
def requests():
    session = SessionLocal()
    q_str = (request.args.get("q") or "").strip()
    try:
        query = session.query(Experiment)
        
        if q_str:
            like = f"%{q_str}%"
            # Search across multiple relevant columns
            query = query.filter(
                or_(
                    Experiment.eln_id.ilike(like),
                    Experiment.project_id.ilike(like),
                    Experiment.project_name.ilike(like),
                    Experiment.theme.ilike(like),
                    Experiment.reaction_type.ilike(like)
                )
            )
        
        experiments = query.order_by(Experiment.id.desc()).all()
        return render_template("requests.html", experiments=experiments, search=q_str)
    finally:
        session.close()

# --- CONFIGURATION: Map Database Columns to Excel Cells ---
CELL_MAPPING = {
    "eln_id": "B2",
    "project_id": "C2",
    "project_name": "D2",
    "theme": "E2",
    "reaction_type": "F2",
    "start_date": "G2",
    "type": "K2",
    "scale_mmol": "O2",
    "conc_moll": "P2",
    "startingmat_1_id": "R2",
    "startingmat_1_eq": "S2",
    # The list driver
    "startingmat_2_id": {"cell": "U2", "direction": "down", "stop_at_empty": True},
    "startingmat_2_eq": {"cell": "V2", "direction": "down", "stop_at_empty": True}, # Change from String to Dict
    # The static individual cells
    "reagent_1_id": "W2",
    "reagent_1_eq": "X2",
    "reagent_2_id": "W3",
    "reagent_2_eq": "X3",
    "reagent_3_id": "W4",
    "reagent_3_eq": "X4",
    "reagent_4_id": "W5",
    "reagent_4_eq": "X5",
    "reagent_5_id": "W6",
    "reagent_5_eq": "X6",
    "solvent_1_id": "Y2",
    "solvent_1_fraction": "Z2",
    "solvent_2_id": "Y3",
    "solvent_2_fraction": "Z3",
}

@app.post("/upload/experiment")
def upload_experiment_data():
    session = SessionLocal()
    try:
        if 'file' not in request.files:
            return jsonify({"ok": False, "error": "No file part"}), 400
            
        file = request.files['file']
        df = pd.read_excel(file, header=None) 
        extracted_values = {}
        
        # 1. Extraction Loop
        for col_name, cell_ref in CELL_MAPPING.items():
            if isinstance(cell_ref, dict):
                col_idx = ord(cell_ref["cell"][0].upper()) - ord('A')
                row_idx = int(cell_ref["cell"][1:]) - 1
                column_slice = df.iloc[row_idx:, col_idx]
                if cell_ref.get("stop_at_empty"):
                    mask = column_slice.isna()
                    column_slice = column_slice[:mask.idxmax() - row_idx] if mask.any() else column_slice
                extracted_values[col_name] = [str(v).strip() for v in column_slice.tolist() if not pd.isna(v)]
            else:
                col_idx = ord(cell_ref[0].upper()) - ord('A')
                row_idx = int(cell_ref[1:]) - 1
                val = df.iloc[row_idx, col_idx] if row_idx < len(df) else None
                extracted_values[col_name] = str(val).strip() if not pd.isna(val) else None

        # 2. Type Logic & Date Formatting
        raw_type = extracted_values.get("type")
        extracted_values["type"] = "Screening" if raw_type == "Test" else "Library"
        raw_date = extracted_values.get("start_date")
        if raw_date:
            extracted_values["start_date"] = str(raw_date)[:10]

        # 3. Pull Driver Lists (Variable component)
        mats_2_ids = extracted_values.pop("startingmat_2_id", [])
        mats_2_eqs = extracted_values.pop("startingmat_2_eq", [])
        
        # 4. Define and Resolve Static Fields
        # We capture the 'raw' value before get_internal_id converts it to a Chem_ID
        id_fields = ["startingmat_1_id", "reagent_1_id", "reagent_2_id", "reagent_3_id", "reagent_4_id", "reagent_5_id", "solvent_1_id", "solvent_2_id"]
        num_fields = ["scale_mmol","conc_moll","startingmat_1_eq", "reagent_1_eq", "reagent_2_eq", "reagent_3_eq", "reagent_4_eq", "reagent_5_eq", "solvent_1_fraction", "solvent_2_fraction"]
        
        static_detail_data = {}
        for f in id_fields:
            raw_input = extracted_values.pop(f, None)
            static_detail_data[f.replace("_id", "_raw")] = raw_input # Store original SMILES/CAS
            static_detail_data[f] = get_internal_id(session, raw_input) # Store resolved Chem_ID
            
        for f in num_fields:
            static_detail_data[f] = parse_float(extracted_values.pop(f, None))

        # 5. Save Parent
        new_experiment = Experiment(**extracted_values)
        session.add(new_experiment)
        session.flush()

        # 6. Save Children (Iterating through the reaction list)
        for i in range(len(mats_2_ids)):
            mat_val = mats_2_ids[i]
            current_mat2_eq = parse_float(mats_2_eqs[i]) if i < len(mats_2_eqs) else None
            
            new_detail = ExperimentDetails(
                experiment_id=new_experiment.id,
                startingmat_2_id=get_internal_id(session, mat_val), # Resolved
                startingmat_2_raw=mat_val,                          # Original
                startingmat_2_eq=current_mat2_eq,
                **static_detail_data
            )
            session.add(new_detail)

        session.commit()

        return jsonify({
            "ok": True, 
            "experiment_id": new_experiment.id, 
            "message": f"File uploaded ({len(mats_2_ids)} reactions). Please enter parameters."
        })

    except Exception as e:
        session.rollback()
        current_app.logger.exception("Upload failed")
        return jsonify({"ok": False, "error": str(e)}), 500
    finally:
        SessionLocal.remove()

@app.post("/upload/finalize-params")
def finalize_params():
    session = SessionLocal()
    data = request.json or {}
    try:
        exp_id = data.get("experiment_id")
        # Update all detail rows associated with this experiment
        session.query(ExperimentDetails).filter(ExperimentDetails.experiment_id == exp_id).update({
            "wavelength_nm": parse_float(data.get("wavelength")),
            "mixing": data.get("mixing"),
            "atmosphere": data.get("atmosphere")
        })
        session.commit()
        return jsonify({"ok": True})
    except Exception as e:
        session.rollback()
        return jsonify({"ok": False, "error": str(e)}), 500
    finally:
        session.close()

@app.post("/upload/registration")
def upload_registration_data():
    session = SessionLocal()
    try:
        if 'file' not in request.files:
            return jsonify({"ok": False, "error": "No file part"}), 400
            
        file = request.files['file']
        df = pd.read_excel(file, header=None)

        # 1. Extract ELN (Cell B2, same as reaction request)
        eln_id = str(df.iloc[1, 1]).strip() if not pd.isna(df.iloc[1, 1]) else None
        
        # 2. Extract Completion Date (Cell H2)
        raw_completion_date = df.iloc[1, 7] # H is index 7
        completion_date = str(raw_completion_date)[:10] if not pd.isna(raw_completion_date) else None
        
        # 3. Extract Planned No. Reactions (Cell I2)
        no_reactions = parse_float(df.iloc[1, 8]) # I is index 8
        
        # 4. Count Registered Compounds (J2 downwards)
        # Column J is index 9. We look at row index 1 (J2) and below.
        column_j = df.iloc[1:, 9]
        # Filter out NaNs or empty strings to get the count
        no_reg_compounds = int(column_j.dropna().astype(str).str.strip().str.len().gt(0).sum())

        # 5. Find and Update the Experiment
        experiment = session.query(Experiment).filter(Experiment.eln_id == eln_id).first()
        if not experiment:
            return jsonify({"ok": False, "error": f"Experiment {eln_id} not found in database."}), 404

        # 6. Apply Calculations and Save
        experiment.completion_date = completion_date
        experiment.no_reactions = int(no_reactions) if no_reactions else 0
        experiment.no_reg_compounds = no_reg_compounds
        
        if experiment.no_reactions and experiment.no_reactions > 0:
            experiment.success_rate = float(no_reg_compounds) / experiment.no_reactions
        else:
            experiment.success_rate = 0.0

        session.commit()
        return jsonify({"ok": True, "message": f"KPIs updated for {eln_id}"})

    except Exception as e:
        session.rollback()
        current_app.logger.exception("Registration upload failed")
        return jsonify({"ok": False, "error": str(e)}), 500
    finally:
        session.close()

# ------------------- Plate Designer Routes ------------------- #

@app.route("/plate_designer")
def plate_designer_ui():
    return render_template("plate_designer.html")

@app.get("/api/plates")
def list_plates():
    session = SessionLocal()
    try:
        # Return templates and recent designs
        templates = session.query(PlateDesign).filter_by(is_template=1).all()
        recent = session.query(PlateDesign).filter_by(is_template=0).order_by(PlateDesign.updated_at.desc()).limit(20).all()
        
        def to_meta(p):
            return {"id": p.id, "name": p.name, "updated_at": str(p.updated_at), "is_template": p.is_template}
            
        return jsonify({
            "ok": True,
            "templates": [to_meta(p) for p in templates],
            "recent": [to_meta(p) for p in recent]
        })
    finally:
        session.close()

@app.post("/api/autofill")
def api_autofill():
    data = request.json or {}
    val = (data.get("value") or "").strip()
    session = SessionLocal()
    try:
        chem_info = _resolve_chemical_data(val)
        if chem_info:
            return jsonify({
                "ok": True,
                "item": chem_info
            })
        return jsonify({"ok": False, "error": "Not found in external registry or local database"})
    finally:
        session.close()

@app.get("/api/chemicals/search")
def api_search_chemicals():
    """
    Robust Typeahead Search
    Query params: q (string), limit (int)
    """
    session = SessionLocal()
    q_str = (request.args.get("q") or request.args.get("query") or "").strip().lower()
    limit = int(request.args.get("limit", 10))
    
    if not q_str:
        return jsonify({"ok": True, "results": []})

    try:
        # Match against multiple fields
        filters = [
            func.lower(Chemical.chem_id).like(f"%{q_str}%"),
            func.lower(Chemical.common_name_abb).like(f"%{q_str}%"),
            func.lower(Chemical.cas).like(f"%{q_str}%"),
            func.lower(Chemical.smiles).like(f"%{q_str}%")
        ]
        
        results = session.query(Chemical).filter(or_(*filters)).limit(limit).all()
        
        data = []
        for c in results:
            data.append({
                "chem_id": c.chem_id,
                "common_name": c.common_name_abb,
                "cas": c.cas,
                "smiles": c.smiles,
                "mw": c.mw
            })
            
        return jsonify({"ok": True, "results": data})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500
    finally:
        session.close()

@app.post("/api/plates")
def save_plate():
    session = SessionLocal()
    try:
        data = request.json
        if not data.get("name"):
            return jsonify({"ok": False, "error": "Name required"}), 400
            
        # Update existing or create new
        if data.get("id"):
            p = session.query(PlateDesign).get(data["id"])
            if not p: return jsonify({"ok": False, "error": "Not found"}), 404
            p.name = data["name"]
            p.assignments = data.get("assignments", [])
            p.plate_metadata = data.get("plate_metadata", {})
            p.plate_type = str(data.get("plate_type", "96"))
            p.is_template = 1 if data.get("is_template") else 0
            p.updated_at = func.now()
        else:
            p = PlateDesign(
                name=data["name"],
                assignments=data.get("assignments", []),
                plate_metadata=data.get("plate_metadata", {}),
                plate_type=str(data.get("plate_type", "96")),
                is_template=1 if data.get("is_template") else 0
            )
            session.add(p)
            
        session.commit()
        return jsonify({"ok": True, "id": p.id})
    except Exception as e:
        session.rollback()
        return jsonify({"ok": False, "error": str(e)}), 500
    finally:
        session.close()

@app.get("/api/plates/<int:pid>")
def load_plate(pid):
    session = SessionLocal()
    try:
        p = session.query(PlateDesign).get(pid)
        if not p: return jsonify({"ok": False, "error": "Not found"}), 404
        return jsonify({
            "ok": True,
            "plate": {
                "id": p.id,
                "name": p.name,
                "assignments": p.assignments,
                "plate_metadata": p.plate_metadata,
                "plate_type": p.plate_type,
                "is_template": p.is_template
            }
        })
    finally:
        session.close()

@app.get("/api/chem_search")
def designer_chem_search():
    # Specialized search for designer (lighter weight, specific fields)
    q = request.args.get("q", "").strip().lower()
    if not q: return jsonify({"ok": True, "results": []})
    
    session = SessionLocal()
    try:
        # Search by ID, Name, CAS, SMILES
        # Limit results
        query = session.query(Chemical).filter(or_(
            func.lower(Chemical.chem_id).like(f"%{q}%"),
            func.lower(Chemical.common_name_abb).like(f"%{q}%"),
            func.lower(Chemical.cas).like(f"%{q}%"),
            func.lower(Chemical.smiles).like(f"%{q}%")
        )).limit(20)
        
        results = []
        for c in query.all():
            results.append({
                "chem_id": c.chem_id,
                "name": c.common_name_abb,
                "cas": c.cas,
                "smiles": c.smiles,
                "mw": c.mw
            })
        return jsonify({"ok": True, "results": results})
    finally:
        session.close()

# ---- Multi-Search ----
@app.post("/chemdb/multisearch")
def chem_multisearch():
    session = SessionLocal()
    data = request.json or {}
    lines = data.get("lines", [])
    
    results = []
    seen_ids = set()

    try:
        for line in lines:
            raw = line.strip()
            if not raw: continue
            
            res = {
                "input": raw,
                "type": "unknown",
                "status": "not_found",
                "matches": []
            }

            # 1. Exact Chem_ID
            if re.fullmatch(r'chem_\d+', raw.lower()):
                res["type"] = "chem_id"
                c = session.query(Chemical).filter(func.lower(Chemical.chem_id) == raw.lower()).first()
                if c:
                    res["status"] = "found"
                    res["matches"].append(c.to_dict())
                    seen_ids.add(c.chem_id)
            
            # 2. CAS
            elif re.match(r"^\d{2,7}-\d{2}-\d$", raw):
                res["type"] = "cas"
                c = session.query(Chemical).filter(Chemical.cas == raw).first()
                if c:
                    res["status"] = "found"
                    res["matches"].append(c.to_dict())
                    seen_ids.add(c.chem_id)

            # 3. SMILES
            else:
                # Try parsing as SMILES
                mol = Chem.MolFromSmiles(raw)
                if mol:
                    res["type"] = "smiles"
                    # Convert to Kekule
                    kekule = iupac_to_kekule_smiles(raw) or raw
                    
                    # Search by SMILES (exact or isomeric)
                    # For simplicity, we search exact string match on stored SMILES or InChIKey
                    # Ideally we use structure search, but here we use text match on canonical/kekule
                    
                    # Try to find by InChIKey (robust)
                    ikey = Chem.MolToInchiKey(mol)
                    c = session.query(Chemical).filter(Chemical.inchi_key == ikey).first()
                    if c:
                        res["status"] = "found"
                        res["matches"].append(c.to_dict())
                        seen_ids.add(c.chem_id)
                    else:
                        # Try text match on SMILES
                        c = session.query(Chemical).filter(Chemical.smiles == kekule).first()
                        if c:
                            res["status"] = "found"
                            res["matches"].append(c.to_dict())
                            seen_ids.add(c.chem_id)

                else:
                    # 4. Name
                    res["type"] = "name"
                    # Exact match first
                    c = session.query(Chemical).filter(func.lower(Chemical.common_name_abb) == raw.lower()).first()
                    if c:
                        res["status"] = "found"
                        res["matches"].append(c.to_dict())
                        seen_ids.add(c.chem_id)
                    else:
                        # Fuzzy / Contains
                        matches = session.query(Chemical).filter(func.lower(Chemical.common_name_abb).like(f"%{raw.lower()}%")).limit(5).all()
                        if matches:
                            res["status"] = "found" # or ambiguous
                            res["matches"] = [m.to_dict() for m in matches]
                            if len(matches) > 1: res["note"] = "Multiple matches"
                            for m in matches: seen_ids.add(m.chem_id)

            results.append(res)

        return jsonify({"ok": True, "results": results})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500
    finally:
        session.close()

# ---- Export Routes ----
@app.route("/export/<fmt>")
def export_data(fmt):
    session = SessionLocal()
    try:
        chem_ids = request.args.getlist("ids")
        fg_filter = request.args.getlist("fg")
        mode = request.args.get("mode", "any")

        q = session.query(Chemical)

        # 1. Filter by IDs if provided
        if chem_ids:
            q = q.filter(Chemical.chem_id.in_(chem_ids))

        # 2. Apply FG Filter
        if fg_filter:
            fg_conditions = []
            for fg in fg_filter:
                fg_conditions.append(cast(Chemical.functional_groups, String).like(f'%"{fg}"%'))
            
            if mode == 'all':
                for cond in fg_conditions:
                    q = q.filter(cond)
            else:
                q = q.filter(or_(*fg_conditions))

        chems = q.all()

        # 3. Generate Data
        data = []
        for c in chems:
            row = {
                "chem_id": c.chem_id,
                "name": c.common_name_abb,
                "cas": c.cas,
                "smiles": c.smiles,
                "mw": c.mw,
                "mim": c.mim,
                "chemform": c.chemform,
                "density": c.density,
                "aggregate_state": c.aggregate_state,
                "stock_solution_c": c.stock_solution_c,
                "inchi": c.inchi,
                "inchi_key": c.inchi_key,
                "functional_groups": c.functional_groups
            }
            data.append(row)

        if fmt == 'csv':
            df = pd.DataFrame(data)
            csv = df.to_csv(index=False)
            from flask import Response
            return Response(
                csv,
                mimetype="text/csv",
                headers={"Content-disposition": "attachment; filename=export.csv"}
            )
        
        elif fmt == 'xlsx':
            df = pd.DataFrame(data)
            from io import BytesIO
            output = BytesIO()
            with pd.ExcelWriter(output, engine='openpyxl') as writer:
                df.to_excel(writer, index=False)
            output.seek(0)
            from flask import send_file
            return send_file(
                output,
                mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
                as_attachment=True,
                download_name='export.xlsx'
            )
        
        elif fmt == 'sdf':
            sdf_str = ""
            for c in chems:
                mol = Chem.MolFromSmiles(c.smiles) if c.smiles else None
                if not mol:
                    mol = Chem.MolFromSmiles("") 
                
                if mol:
                    try:
                        rdMolDescriptors.CalcNumRotatableBonds(mol)
                        from rdkit.Chem import AllChem
                        AllChem.Compute2DCoords(mol)
                    except: pass
                    
                    mol.SetProp("chem_id", str(c.chem_id))
                    mol.SetProp("name", str(c.common_name_abb or ""))
                    if c.cas: mol.SetProp("cas", str(c.cas))
                    if c.smiles: mol.SetProp("smiles", str(c.smiles))
                    if c.mw: mol.SetProp("mw", str(c.mw))
                    
                    sdf_str += Chem.MolToMolBlock(mol)
                    for k, v in {
                        "chem_id": c.chem_id,
                        "name": c.common_name_abb,
                        "cas": c.cas,
                        "smiles": c.smiles,
                        "mw": c.mw,
                        "mim": c.mim,
                        "chemform": c.chemform
                    }.items():
                        if v:
                            sdf_str += f"> <{k}>\n{v}\n\n"
                    sdf_str += "$$$$\n"
            
            from flask import Response
            return Response(
                sdf_str,
                mimetype='chemical/x-mdl-sdfile',
                headers={"Content-disposition": "attachment; filename=export.sdf"}
            )

        else:
            return "Unknown format", 400

    except Exception as e:
        current_app.logger.exception("Export failed")
        return str(e), 500
    finally:
        session.close()

# 4. Only ONE entry point at the very bottom

@app.post("/plates/export_surf")
def export_surf_file():
    from io import BytesIO
    session = SessionLocal()
    try:
        data = request.json or {}
        plate_type = int(data.get("plate_type", 96))
        meta = data.get("meta", {})
        assignments = data.get("assignments", [])

        # 1. Define Wells
        rows_str = "ABCDEFGH"
        cols_num = 12
        if plate_type == 48:
            rows_str = "ABCDEF"; cols_num = 8
        elif plate_type == 24:
            rows_str = "ABCD"; cols_num = 6
        
        all_wells = []
        for r in rows_str:
            for c in range(1, cols_num + 1):
                all_wells.append(f"{r}{c}")
        
        # 2. Resolve Wells (Initialize)
        resolved = { w: {} for w in all_wells } # well_id -> dict of fields
        locked = { w: {} for w in all_wells }   # well_id -> set of locked fields

        def get_targets(scope, target):
            if scope == 'plate': return all_wells
            if scope == 'row': return [w for w in all_wells if w.startswith(target)]
            if scope == 'col': return [w for w in all_wells if w[1:] == str(target)]
            if scope == 'well': return [target] if target in resolved else []
            if scope == 'selection': return [t for t in target if t in resolved]
            return []

        # 3. Apply Assignments
        for layer in assignments:
            scope = layer.get("scope")
            target = layer.get("target")
            role = layer.get("role")
            ldata = layer.get("data")
            behavior = layer.get("behavior", "overwrite") # overwrite, empty, lock

            targets = get_targets(scope, target)
            
            fields_to_update = []
            if role == 'condition':
                fields_to_update = ['temperature', 'time', 'atmosphere']
            else:
                fields_to_update = [role]
            
            for wid in targets:
                w_data = resolved[wid]
                w_locked = locked[wid]

                for field in fields_to_update:
                    if field in w_locked: continue # Locked
                    if behavior == 'empty' and w_data.get(field): continue # Skip if not empty

                    val = None
                    if role == 'condition':
                        val = ldata.get(field)
                    else:
                        val = ldata
                    
                    if val is not None and val != "":
                        w_data[field] = val
                    
                    if behavior == 'lock':
                        w_locked[field] = True # Mark as locked (not actual value, just flag)

        # 4. Resolve Chemicals (Batch)
        ids_to_resolve = set()
        roles = ['startingmat', 'aryl', 'alkyl', 'ni_cat', 'ir_cat', 'ligand', 'additive', 'silane', 'solvent']
        for w in resolved.values():
            for role in roles:
                c = w.get(role)
                if c and isinstance(c, dict) and c.get("chem_id"):
                    ids_to_resolve.add(c.get("chem_id"))
        
        chem_map = {} 
        if ids_to_resolve:
            chems = session.query(Chemical).filter(Chemical.chem_id.in_(list(ids_to_resolve))).all()
            for c in chems:
                chem_map[c.chem_id] = c
        
        # 5. Build Rows
        final_rows = []
        for wid in all_wells:
            w = resolved[wid]
            
            def get_c(role, attr):
                c_data = w.get(role)
                if not c_data: return None
                if attr == 'eq': return c_data.get('eq')
                
                cid = c_data.get('chem_id') # Identify by chem_id
                db_c = chem_map.get(cid)
                if db_c:
                    if attr == 'name': return db_c.common_name_abb
                    if attr == 'cas': return db_c.cas
                    if attr == 'smiles': return db_c.smiles
                
                return c_data.get(attr) # Fallback

            row = {}
            row["eln_id"] = meta.get("eln_id")
            row["rxn_date"] = meta.get("rxn_date")
            row["rxn_type"] = "Screening"
            row["rxn_name"] = data.get("name", "Untitled")
            row["rxn_tech"] = meta.get("reaction_tech")
            row["temperature_deg_c"] = w.get("temperature")
            row["time_h"] = w.get("time")
            row["atmosphere"] = w.get("atmosphere") or meta.get("atmosphere")
            row["stirring_shaking"] = meta.get("mixing")
            row["scale_mol"] = meta.get("scale")
            row["concentration_mol_l"] = meta.get("concentration")
            row["wavelength_nm"] = meta.get("wavelength")
            
            row["startingmat_1_name"] = get_c("startingmat", "name")
            row["startingmat_1_cas"] = get_c("startingmat", "cas")
            row["startingmat_1_smiles"] = get_c("startingmat", "smiles")
            row["startingmat_1_eq"] = get_c("startingmat", "eq")
            
            row["reagent_1_name"] = get_c("aryl", "name")
            row["reagent_1_cas"] = get_c("aryl", "cas")
            row["reagent_1_smiles"] = get_c("aryl", "smiles")
            row["reagent_1_eq"] = get_c("aryl", "eq")
            
            row["reagent_2_name"] = get_c("alkyl", "name")
            row["reagent_2_cas"] = get_c("alkyl", "cas")
            row["reagent_2_smiles"] = get_c("alkyl", "smiles")
            row["reagent_2_eq"] = get_c("alkyl", "eq")
            
            cat_role = "ni_cat" if w.get("ni_cat") else "ir_cat"
            row["catalyst_1_name"] = get_c(cat_role, "name")
            row["catalyst_1_cas"] = get_c(cat_role, "cas")
            row["catalyst_1_smiles"] = get_c(cat_role, "smiles")
            row["catalyst_1_eq"] = get_c(cat_role, "eq")
            
            row["ligand_1_name"] = get_c("ligand", "name")
            row["ligand_1_cas"] = get_c("ligand", "cas")
            row["ligand_1_smiles"] = get_c("ligand", "smiles")
            row["ligand_1_eq"] = get_c("ligand", "eq")
            
            add_role = "additive" if w.get("additive") else "silane"
            row["additive_1_name"] = get_c(add_role, "name")
            row["additive_1_cas"] = get_c(add_role, "cas")
            row["additive_1_smiles"] = get_c(add_role, "smiles")
            row["additive_1_eq"] = get_c(add_role, "eq")
            
            row["solvent_1_name"] = get_c("solvent", "name")
            row["solvent_1_cas"] = get_c("solvent", "cas")
            row["solvent_1_smiles"] = get_c("solvent", "smiles")
            row["solvent_1_fraction"] = 1.0 
            
            final_rows.append(row)

        df = pd.DataFrame(final_rows)
        cols = [
            "eln_id", "rxn_date", "rxn_type", "rxn_name", "rxn_tech",
            "temperature_deg_c", "time_h", "atmosphere", "stirring_shaking",
            "scale_mol", "concentration_mol_l", "wavelength_nm",
            "startingmat_1_name", "startingmat_1_cas", "startingmat_1_smiles", "startingmat_1_eq",
            "reagent_1_name", "reagent_1_cas", "reagent_1_smiles", "reagent_1_eq",
            "reagent_2_name", "reagent_2_cas", "reagent_2_smiles", "reagent_2_eq",
            "catalyst_1_name", "catalyst_1_cas", "catalyst_1_smiles", "catalyst_1_eq",
            "ligand_1_name", "ligand_1_cas", "ligand_1_smiles", "ligand_1_eq",
            "additive_1_name", "additive_1_cas", "additive_1_smiles", "additive_1_eq",
            "solvent_1_name", "solvent_1_cas", "solvent_1_smiles", "solvent_1_fraction"
        ]
        # Reindex to ensure structure
        df = df.reindex(columns=cols)
        
        output = BytesIO()
        with pd.ExcelWriter(output, engine='openpyxl') as writer:
            df.to_excel(writer, index=False)
        output.seek(0)
        
        from flask import send_file
        return send_file(
            output,
            mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
            as_attachment=True,
            download_name=f"SURF_{meta.get('eln_id', 'export')}.xlsx"
        )

    except Exception as e:
        session.close()
        current_app.logger.exception("SURF Export failed")
        return jsonify({"ok": False, "error": str(e)}), 500
    finally:
        session.close()

@app.get("/analysis-tool")
def analysis_tool():
    return render_template("analysis_tool.html")

@app.post("/api/analysis/process")
def analysis_process():
    session = SessionLocal()
    try:
        if 'file' not in request.files:
            return jsonify({"ok": False, "error": "No file uploaded"}), 400
        
        file = request.files['file']
        if not file.filename.endswith('.sdf'):
             return jsonify({"ok": False, "error": "Invalid file format. Please upload .sdf"}), 400

        # RDKit Parsing
        # Save to temp or read from stream? RDKit accepts file-like object? 
        # RDKit SDMolSupplier needs a filename or strict stream. 
        # Forward stream wrapper is safer with string data
        
        mol_supplier = None
        # Option 1: Save temp
        import tempfile
        with tempfile.NamedTemporaryFile(delete=False, suffix='.sdf') as tf:
            file.save(tf.name)
            tf_path = tf.name
        
        try:
            mol_supplier = Chem.SDMolSupplier(tf_path)
            
            internal_mosaic = []
            internal_rmm = []
            external = []
            
            stats = {"internal": 0, "external": 0, "matches": 0, "orders": 0}

            for mol in mol_supplier:
                if not mol: continue
                
                # Extract Props
                # User Request:
                # "Supplier Name of Min Lead Time BS" -> Roche/Roche Basel (case insensitive) -> Internal
                # "Supplier Substance ID of Min Lead Time BS" -> Starts with RO -> Mosaic, else RMM
                
                props = mol.GetPropsAsDict()
                
                # Try specific keys first, fall back to generic if not found (optional, but good for robustness if file varies)
                # But user was specific about these keys not working.
                
                supp_name_raw = str(props.get('Supplier Name of Min Lead Time BS', props.get('Supplier', ''))).strip()
                supp_id_raw = str(props.get('Supplier Substance ID of Min Lead Time BS', props.get('ID', props.get('V_ID', '')))).strip()
                
                # Categorize
                # Internal if "Roche Basel" or "Roche" (no case sensitive)
                supp_upper = supp_name_raw.upper()
                is_internal = (supp_upper == "ROCHE BASEL" or supp_upper == "ROCHE")
                
                category = "external"
                if is_internal:
                    stats['internal'] += 1
                    if supp_id_raw.startswith("RO"): 
                        category = "mosaic"
                    else: 
                        category = "rmm"
                else:
                    stats['external'] += 1
                    category = "external"
                
                # Structures from RDKit
                try:
                    smi = Chem.MolToSmiles(mol)
                    ichi = Chem.MolToInchi(mol)
                    ikey = Chem.MolToInchiKey(mol)
                except:
                    smi = ""; ichi = ""; ikey = ""
                
                iupac = str(props.get('IUPAC Name', '')).strip()

                # DB Match logic remains same...
                
                # DB Match
                # Logic: Match on InChIKey (Strong), InChI (Strong), SMILES (Medium), IUPAC (Weak)
                # Let's try InChIKey first
                match = None
                if ikey:
                    match = session.query(Chemical).filter(Chemical.inchi_key == ikey).first()
                
                if not match and smi:
                     match = session.query(Chemical).filter(Chemical.smiles == smi).first()
                
                # Check Inventory
                matched = False
                chem_id_link = ""
                batches_count = 0
                locations = []
                
                if match:
                    matched = True
                    stats['matches'] += 1
                    chem_id_link = match.chem_id
                    
                    # Batch Check
                    batches = session.query(Batch).filter(Batch.chem_id == match.chem_id, Batch.status == "Available").all()
                    batches_count = len(batches)
                    locations = list(set([b.location for b in batches]))
                else:
                    stats['orders'] += 1

                row = {
                    "supplier_id": supp_id_raw,
                    "matched": matched,
                    "chem_id": chem_id_link,
                    "batches": batches_count,
                    "location": ", ".join(locations),
                    "iupac": iupac,
                    "smiles": smi,
                    "inchi": ichi
                }
                
                if category == "mosaic": internal_mosaic.append(row)
                elif category == "rmm": internal_rmm.append(row)
                else: external.append(row)

            return jsonify({
                "ok": True,
                "stats": stats,
                "internal_mosaic": internal_mosaic,
                "internal_rmm": internal_rmm,
                "external": external
            })

        finally:
            if os.path.exists(tf_path):
                os.remove(tf_path)

    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({"ok": False, "error": str(e)}), 500
    finally:
        session.close()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8000, debug=True)
