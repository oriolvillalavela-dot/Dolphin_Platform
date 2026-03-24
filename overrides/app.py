
import os
from datetime import datetime, date
from flask import Flask, render_template, request, jsonify, redirect, url_for
from sqlalchemy import create_engine, func, or_, text as sq_text, cast, String
from sqlalchemy.engine import URL
from sqlalchemy.orm import sessionmaker, scoped_session
from models import Base, Chemical, Supplier, Bottle, Batch, Plate, PlateWellReagent, SurfRow

app = Flask(__name__)
app.secret_key = os.getenv("SECRET_KEY", "change_me")

# --- DB config from env ---
DB_NAME = os.getenv("DB_NAME")
DB_HOST = os.getenv("DB_HOST", "localhost")
DB_PORT = os.getenv("DB_PORT", "5432")
DB_USER = os.getenv("DB_USER")
DB_PASSWORD = os.getenv("DB_PASSWORD")

db_url = URL.create(
    drivername="postgresql+psycopg2",
    username=DB_USER,
    password=DB_PASSWORD,
    host=DB_HOST,
    port=int(DB_PORT) if DB_PORT else None,
    database=DB_NAME,
)

engine = create_engine(db_url, pool_pre_ping=True)
SessionLocal = scoped_session(sessionmaker(bind=engine, autoflush=False, autocommit=False))

# --- Schema bootstrap + lightweight migration ---
_schema_ready = False
def ensure_schema():
    global _schema_ready
    if _schema_ready:
        return
    Base.metadata.create_all(engine)
    with engine.begin() as conn:
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
            ("plate_id", "INTEGER")
        ]:
            conn.execute(sq_text(f'ALTER TABLE IF EXISTS surf ADD COLUMN IF NOT EXISTS {col} {ddl}'))
    _schema_ready = True
    try:
        conn.execute(sq_text('ALTER TABLE batch_db ALTER COLUMN concentration_moll DROP NOT NULL'))
    except Exception:
        pass

@app.before_request
def _ensure():
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

# -------- Routes --------
@app.route("/")
def home():
    return redirect(url_for("chemicals"))

@app.get("/health")
def health():
    try:
        with engine.connect() as conn:
            conn.execute(sq_text("SELECT 1"))
        return {"status": "ok"}
    except Exception as e:
        return {"status": "error", "detail": str(e)}, 500

# ---- Chemicals DB ----
@app.get("/chemicals")
def chemicals():
    session = SessionLocal()
    q = session.query(Chemical).order_by(Chemical.chem_id.desc())
    search = request.args.get("q", "").strip()
    if search:
        like = f"%{search.lower()}%"
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
    if not (data.get("common_name_abb")):
        return jsonify({"ok": False, "error": "Missing field: common_name_abb"}), 400

    chem = Chemical(
        chem_id=next_chem_id(session),
        common_name_abb=data.get("common_name_abb","").strip(),
        cas=(data.get("cas") or "").strip() or None,
        ro_srn=(data.get("ro_srn") or "").strip() or None,
        chemform=(data.get("chemform") or "").strip() or None,
        mw=parse_float(data.get("mw")),
        mim=(data.get("mim") or "").strip() or None,
        density=parse_float(data.get("density")),
        aggregate_state=(data.get("aggregate_state") or "").strip() or None,
        stock_solution_c=(data.get("stock_solution_c") or "").strip() or None,
        smiles=(data.get("smiles") or "").strip() or None,
        inchi=(data.get("inchi") or "").strip() or None,
        inchi_key=(data.get("inchi_key") or "").strip() or None,
    )
    try:
        session.add(chem)
        session.commit()
        return jsonify({"ok": True, "chem": chem.to_dict()})
    except Exception as e:
        session.rollback()
        return jsonify({"ok": False, "error": str(e)}), 500
    finally:
        session.close()

@app.get("/chemicals/<chem_id>/availability")
def chem_availability(chem_id):
    session = SessionLocal()
    try:
        for bt in session.query(Batch).filter(Batch.status != "Expired", Batch.expiring_date != None).all():
            if bt.expiring_date and bt.expiring_date <= today():
                bt.status = "Expired"
        session.commit()

        batches = (session.query(Batch)
                   .filter(Batch.chem_id == chem_id,
                           Batch.status.in_(["Available", "Stock Room"]))
                   .order_by(Batch.batch_id.asc())
                   .all())
        out = [{"batch_id": b.batch_id, "location": b.location, "sublocation": b.sublocation or ""} for b in batches]
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

# ---- Bottles DB ----
@app.get("/bottles")
def bottles():
    session = SessionLocal()
    qstr = request.args.get("q","").strip().lower()
    q = session.query(Bottle).order_by(Bottle.created_at.desc())
    if qstr:
        like = f"%{qstr}%"
        q = q.filter(or_(
            func.lower(Bottle.bottle_id).like(like),
            func.lower(Bottle.chem_id).like(like),
            func.lower(Bottle.supplier_id).like(like),
            func.lower(Bottle.lot_no).like(like),
        ))
    rows = q.all()
    return render_template("bottles.html", bottles=rows, search=qstr)

@app.post("/bottles/create/<chem_id>")
def create_bottle(chem_id):
    session = SessionLocal()
    data = request.json or {}
    for k in ["supplier_id","Lot_no","purity","size_amount"]:
        if not data.get(k):
            return jsonify({"ok": False, "error": f"Missing {k}"}), 400

    sname = data["supplier_id"].strip()
    if not session.query(Supplier).filter(func.lower(Supplier.name)==sname.lower()).first():
        session.add(Supplier(name=sname))
        session.flush()

    next_no = next_bottle_suffix(session, chem_id)
    bottle_id = f"{chem_id}_B{next_no}"

    bottle = Bottle(
        bottle_id=bottle_id,
        chem_id=chem_id,
        supplier_id=sname,
        lot_no=data["Lot_no"].strip(),
        purity=parse_float(data["purity"]),
        size_amount=data["size_amount"].strip(),
    )
    try:
        session.add(bottle)
        session.commit()
        return jsonify({"ok": True, "bottle_id": bottle_id, "bottle_no": next_no})
    except Exception as e:
        session.rollback()
        return jsonify({"ok": False, "error": str(e)}), 500
    finally:
        session.close()

# ---- Batch DB ----
@app.get("/batches")
def batches():
    session = SessionLocal()
    for bt in session.query(Batch).filter(Batch.status != "Expired", Batch.expiring_date != None).all():
        if bt.expiring_date and bt.expiring_date <= today():
            bt.status = "Expired"
    session.commit()

    qstr = request.args.get("q","").strip().lower()
    q = session.query(Batch).order_by(Batch.created_at.desc())
    if qstr:
        like = f"%{qstr}%"
        q = q.filter(or_(
            func.lower(Batch.batch_id).like(like),
            func.lower(Batch.chem_id).like(like),
            func.lower(Batch.barcode).like(like),
            func.lower(Batch.location).like(like),
            func.lower(Batch.sublocation).like(like),
            func.lower(Batch.status).like(like),
        ))
    rows = q.all()
    return render_template("batches.html", batches=rows, search=qstr)

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
        concentration_moll=parse_float(data.get("concentration_moll")),
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

    batch = Batch(
        batch_id=batch_id,
        chem_id=chem_id,
        kind=data["Type"],
        bottle_no=bottle_no,
        kind_index=next_no,
        concentration_moll=parse_float(data.get("concentration_moll")),
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

        loc = (data.get("location") or "").strip()
        subloc = (data.get("sublocation") or "").strip() or None
        status = (data.get("status") or "").strip()

        if status == "Stock Room":
            loc = "Stock Room"
        if status not in ["Available","Empty","Stock Room","Expired"]:
            return jsonify({"ok": False, "error": "Invalid status"}), 400

        exp_ddmmyyyy = (data.get("expiring_date_ddmmyyyy") or "").strip()
        if bt.kind == "Stock solution" and status == "Available":
            if not exp_ddmmyyyy:
                return jsonify({"ok": False, "error": "Missing expiring date (dd/mm/yyyy) for Stock solution"}), 400
            try:
                dd, mm, yy = [int(x) for x in exp_ddmmyyyy.split("/")]
                bt.expiring_date = date(yy, mm, dd)
            except Exception:
                return jsonify({"ok": False, "error": "Invalid expiring date format; expected dd/mm/yyyy"}), 400

        bt.location = loc
        bt.sublocation = subloc
        bt.status = status
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


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8000, debug=True)
