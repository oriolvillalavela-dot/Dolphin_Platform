import os
import re
from flask import Blueprint, request, jsonify, current_app, send_file, render_template
from sqlalchemy import func
import io
from io import BytesIO
from collections import Counter
from .utils import load_purif_methods, get_user_id, validate_eln_inputs, save_purif_methods
from models import Chemist, ELN, IPCMeasurement, PurifMeasurement, Chemical
from database import SessionLocal

lc_ms_api_bp = Blueprint('lc_ms_api', __name__, url_prefix='/api/lc-ms')

# TARGET NETWORK DIRECTORY
# Removed network saving completely

RECENT_LIMIT = 20

@lc_ms_api_bp.route("/healthz", methods=["GET"])
def healthz():
    return "ok", 200

# -------- Chemists API -------- #
@lc_ms_api_bp.route("/chemists", methods=["GET", "POST"])
def chemists_collection():
    session_db = SessionLocal()
    try:
        if request.method == "GET":
            items = [{"username": c.username, "user_id": c.user_id} for c in session_db.query(Chemist).order_by(Chemist.username.asc()).all()]
            return jsonify(items)
        data = request.get_json(force=True, silent=True) or {}
        username = (data.get("username") or "").strip().lower()
        user_id = (data.get("user_id") or "").strip().upper()
        if not username or not user_id: return jsonify({"error":"username and user_id are required"}), 400
        if session_db.query(Chemist).filter_by(username=username).first(): return jsonify({"error":"Chemist already exists"}), 409
        session_db.add(Chemist(username=username, user_id=user_id))
        session_db.commit()
        return jsonify({"ok": True})
    finally:
        session_db.close()

@lc_ms_api_bp.route("/chemists/<username>", methods=["DELETE"])
def chemist_delete(username):
    session_db = SessionLocal()
    try:
        username = (username or "").strip().lower()
        c = session_db.query(Chemist).filter_by(username=username).first()
        if not c: return jsonify({"error":"Chemist not found"}), 404
        if session_db.query(ELN).filter_by(chemist=username).first(): return jsonify({"error":"Cannot delete chemist that is referenced by existing ELNs."}), 400
        session_db.delete(c)
        session_db.commit()
        return jsonify({"ok": True})
    finally:
        session_db.close()

# -------- ELNs API -------- #
@lc_ms_api_bp.route("/elns", methods=["GET"])
def get_elns():
    session_db = SessionLocal()
    try:
        q = request.args.get("q", "").strip()
        limit_arg = request.args.get("limit")
        offset_arg = request.args.get("offset", "0")
        
        query = session_db.query(ELN).order_by(func.coalesce(ELN.order_id, 0).desc(), ELN.eln_id.desc())
        
        if q:
            like = f"%{q.lower()}%"
            query = query.filter(func.lower(ELN.eln_id).like(like))
            
        total = query.count()
            
        if limit_arg:
            try:
                limit = int(limit_arg)
                offset = int(offset_arg)
                query = query.limit(limit).offset(offset)
            except ValueError:
                pass
                
        results = []
        for eln in query.all():
            results.append({
                "eln_id": eln.eln_id,
                "chemist": eln.chemist,
                "stmat_1_chemform": eln.stmat_1_chemform,
                "stmat_2_chemform": eln.stmat_2_chemform,
                "product_1_chemform": eln.product_1_chemform,
                "product_2_chemform": eln.product_2_chemform,
                "product_3_chemform": eln.product_3_chemform,
                "product_4_chemform": eln.product_4_chemform,
                "order_id": eln.order_id
            })
            
        return jsonify({
            "items": results,
            "total": total
        })
    finally:
        session_db.close()

@lc_ms_api_bp.route("/elns", methods=["POST"])
def add_eln():
    session_db = SessionLocal()
    try:
        data = request.get_json(force=True, silent=True) or {}
        errors = validate_eln_inputs(data)
        if errors:
            return jsonify({"error": "; ".join(errors)}), 400
            
        eln_id = data.get("eln_id", "").strip()
        chemist = data.get("chemist", "").strip().lower()
        
        if session_db.query(Chemist).filter_by(username=chemist).first() is None:
            return jsonify({"error": f"Chemist '{chemist}' is not in the roster."}), 400
            
        if session_db.query(ELN).get(eln_id):
            return jsonify({"error": f"ELN {eln_id} already exists."}), 409

        next_id = (session_db.query(func.coalesce(func.max(ELN.order_id), 0)).scalar() or 0) + 1

        rec = ELN(
            eln_id=eln_id, chemist=chemist,
            stmat_1_chemform=data.get("stmat_1_chemform") or None,
            stmat_2_chemform=data.get("stmat_2_chemform") or None,
            product_1_chemform=data.get("product_1_chemform") or None,
            product_2_chemform=data.get("product_2_chemform") or None,
            product_3_chemform=data.get("product_3_chemform") or None,
            product_4_chemform=data.get("product_4_chemform") or None,
            order_id=next_id,
        )
        session_db.add(rec)
        session_db.commit()
        return jsonify({"ok": True, "eln_id": eln_id})
    finally:
        session_db.close()

@lc_ms_api_bp.route("/elns/<eln_id>", methods=["PATCH"])
def api_update_eln(eln_id: str):
    session_db = SessionLocal()
    try:
        m = session_db.query(ELN).get(eln_id)
        if not m: return jsonify({"error":"Not found"}), 404
        data = request.get_json(force=True, silent=True) or {}
        for key in ["stmat_1_chemform","stmat_2_chemform","product_1_chemform","product_2_chemform","product_3_chemform","product_4_chemform"]:
            if key in data: setattr(m, key, (data[key] or "").strip() or None)
        session_db.commit()
        return jsonify({"ok": True})
    finally:
        session_db.close()

# -------- Chemicals Lookup API (Phase 1 & 2) -------- #
@lc_ms_api_bp.route("/chem/<chem_id>", methods=["GET"])
def get_chemical_formula(chem_id):
    session_db = SessionLocal()
    try:
        chem_id = str(chem_id).strip()
        if not chem_id:
            return jsonify({"error": "chem_id is required"}), 400
            
        chem = session_db.query(Chemical).filter(Chemical.chem_id.ilike(chem_id)).first()
        if not chem:
            return jsonify({"error": "Chemical not found"}), 404
            
        return jsonify({
            "chem_id": chem.chem_id,
            "formula": chem.chemform or ""
        })
    finally:
        session_db.close()

@lc_ms_api_bp.route("/calculate-formula", methods=["POST"])
def calculate_formula():
    data = request.get_json(force=True, silent=True) or {}
    smiles = data.get("smiles", "").strip()
    if not smiles:
        return jsonify({"error": "SMILES string is required"}), 400
        
    try:
        from rdkit import Chem
        from rdkit.Chem import rdMolDescriptors
        
        mol = Chem.MolFromSmiles(smiles)
        if not mol:
            # Maybe it's not SMILES but an SDF/MolBlock (JSME output often is a SMILES or Mol file)
            # We will try MolFromMolBlock if SMILES fails but has newlines
            if "\n" in smiles:
                mol = Chem.MolFromMolBlock(smiles)
                
            if not mol:
                return jsonify({"error": "Invalid structure or SMILES string"}), 400
                
        formula = rdMolDescriptors.CalcMolFormula(mol)
        return jsonify({"formula": formula})
    except ImportError:
        return jsonify({"error": "RDKit is not installed on the server."}), 500
@lc_ms_api_bp.route("/upload-structure", methods=["POST"])
def upload_structure():
    if 'file' not in request.files:
        return jsonify({"error": "No file uploaded"}), 400
    file = request.files['file']
    if file.filename == '':
        return jsonify({"error": "No selected file"}), 400
    
    try:
        content = file.read().decode('utf-8')
        from rdkit import Chem
        from rdkit.Chem import rdMolDescriptors
        
        mol = Chem.MolFromMolBlock(content)
        if not mol:
            # Fallback if it's not a mol block
            return jsonify({"error": "Invalid structure file or format not supported by RDKit MolFromMolBlock. Please upload a .mol or .sdf file."}), 400
            
        formula = rdMolDescriptors.CalcMolFormula(mol)
        return jsonify({"formula": formula})
    except ImportError:
        return jsonify({"error": "RDKit is not installed on the server."}), 500
    except Exception as e:
        return jsonify({"error": str(e)}), 400

@lc_ms_api_bp.route("/upload-reaction", methods=["POST"])
def upload_reaction():
    if 'file' not in request.files:
        return jsonify({"error": "No file uploaded"}), 400
    file = request.files['file']
    if file.filename == '':
        return jsonify({"error": "No selected file"}), 400
        
    try:
        content = file.read().decode('utf-8')
        from rdkit import Chem
        from rdkit.Chem import rdChemReactions
        from rdkit.Chem import rdMolDescriptors
        
        rxn = rdChemReactions.ReactionFromRxnBlock(content)
        if not rxn:
            return jsonify({"error": "Invalid reaction file. Please upload a valid .rxn format."}), 400
            
        session_db = SessionLocal()
        try:
            reactants = []
            for i in range(rxn.GetNumReactantTemplates()):
                mol = rxn.GetReactantTemplate(i)
                try: Chem.SanitizeMol(mol)
                except: pass
                
                frags = Chem.GetMolFrags(mol, asMols=True)
                for frag in frags:
                    try: Chem.SanitizeMol(frag)
                    except: pass
                    formula = rdMolDescriptors.CalcMolFormula(frag)
                    inchi_key = Chem.MolToInchiKey(frag)
                    
                    match = session_db.query(Chemical).filter(Chemical.inchi_key == inchi_key).first()
                    if match:
                        reactants.append({"id": match.chem_id, "formula": match.chemform or formula})
                    else:
                        reactants.append({"id": "", "formula": formula})
                    
            products = []
            for i in range(rxn.GetNumProductTemplates()):
                mol = rxn.GetProductTemplate(i)
                try: Chem.SanitizeMol(mol)
                except: pass
                
                frags = Chem.GetMolFrags(mol, asMols=True)
                for frag in frags:
                    try: Chem.SanitizeMol(frag)
                    except: pass
                    formula = rdMolDescriptors.CalcMolFormula(frag)
                    products.append({"formula": formula})
                
            return jsonify({
                "reactants": reactants,
                "products": products
            })
        finally:
            session_db.close()
            
    except ImportError:
        return jsonify({"error": "RDKit is not installed on the server."}), 500
    except Exception as e:
        return jsonify({"error": f"Error parsing reaction file: {str(e)}"}), 500

# -------- Purif Methods API -------- #
@lc_ms_api_bp.route("/purif-methods", methods=["GET"])
def get_purif_methods():
    methods = load_purif_methods()
    return jsonify(methods)

@lc_ms_api_bp.route("/purif-methods", methods=["POST"])
def add_purif_method():
    data = request.get_json(force=True, silent=True) or {}
    new_method = (data.get("name") or "").strip()
    if not new_method:
        return jsonify({"error": "Method name is required."}), 400
        
    methods = load_purif_methods()
    # Case-insensitive check
    if new_method.lower() not in [m.lower() for m in methods]:
        methods.append(new_method)
        save_purif_methods(methods)
        
    return jsonify({"ok": True, "method": new_method})

# -------- Generation APIs -------- #
@lc_ms_api_bp.route("/generate/ipc", methods=["POST"])
def generate_ipc():
    """ Expects JSON body: { rows: [{eln_id, ipc_no, duration_h}] } OR Form-Data (eln_id[], exp_no[], duration_h[]) """
    rows_data = []
    
    # 1. Attempt strict JSON parsing
    if request.is_json or (request.data and request.data.strip().startswith(b"{")):
        try:
            data = request.get_json(force=True, silent=True) or {}
            rows_data = data.get("rows", [])
        except Exception:
            pass

    # 2. Attempt legacy Form-Data parsing (ipc.html standalone view)
    if not rows_data and request.form:
        eln_ids = request.form.getlist("eln_id[]")
        ipc_nos = request.form.getlist("exp_no[]") or request.form.getlist("ipc_no[]")
        dur_hs = request.form.getlist("duration_h[]")
        
        for e, idx, d in zip(eln_ids, ipc_nos, dur_hs):
            e_str = str(e).strip()
            if e_str:
                rows_data.append({
                    "eln_id": e_str, 
                    "ipc_no": str(idx).strip() or "1", 
                    "duration_h": str(d).strip() or "1"
                })

    if not rows_data:
        return jsonify({"error": "Please provide at least one valid ELN row. Unrecognized payload format: " + request.get_data(as_text=True)}), 400

    session_db = SessionLocal()
    try:
        found_map = {}
        missing = []
        for r in rows_data:
            eln = str(r.get("eln_id", "")).strip()
            if not eln: continue
            rec = session_db.query(ELN).get(eln)
            if rec:
                found_map[eln] = rec
            else:
                missing.append(eln)

        if missing:
            return jsonify({"error": f"These ELNs are not in the database: {', '.join(missing)}"}), 400

        chem_list = [found_map[str(r.get("eln_id"))].chemist for r in rows_data if str(r.get("eln_id")) in found_map]
        chem_counts = Counter(chem_list)
        if len(chem_counts) != 1:
            return jsonify({"error": "All provided ELNs must belong to the same chemist."}), 400

        chemist_username = chem_list[0]
        user_id = get_user_id(chemist_username)
        if not user_id:
            return jsonify({"error": f"Chemist '{chemist_username}' has no configured userID mapping."}), 400

        errors = []
        lines_data = [] # hold data until all validation passes
        
        for idx, r in enumerate(rows_data):
            eln = str(r.get("eln_id", "")).strip()
            exp_val = r.get("ipc_no", "")
            dur_val = r.get("duration_h", "")
            
            try:
                exp_i = int(float(exp_val))
            except (ValueError, TypeError):
                errors.append(f"Row {idx+1}: IPC no. '{exp_val}' is not a valid integer.")
                continue
                
            try:
                dur_i = int(float(dur_val))
            except (ValueError, TypeError):
                dur_i = 1 # default if empty or invalid according to original spec, but let's be strict if they gave something weird
                
            existing = session_db.query(IPCMeasurement).filter_by(eln_id=eln, ipc_no=exp_i).first()
            if existing:
                max_ipc = session_db.query(func.max(IPCMeasurement.ipc_no)).filter_by(eln_id=eln).scalar() or 0
                next_ipc = max_ipc + 1
                errors.append(f"{eln}: IPC no. {exp_i} already used. Suggested next IPC no.: {next_ipc}.")
                continue
                
            lines_data.append((eln, exp_i, dur_i))

        if errors:
            return jsonify({"error": "\n".join(errors)}), 400

        # Build TSV and persist
        lines = []
        vial = 1
        for eln, exp_i, dur_i in lines_data:
            rec = found_map[eln]
            session_db.add(IPCMeasurement(
                chemist_username=chemist_username,
                eln_id=eln,
                ipc_no=exp_i,
                duration_h=dur_i
            ))
            label = f"{eln}_{exp_i}_{dur_i}h"
            cols = [str(vial), label] + rec.chemform_list()
            lines.append("\t".join(cols))
            vial += 1

        content = "\n".join(lines)
        filename = f"{user_id}_IPC.txt"
        
        # Commit DB records
        session_db.commit()

        return jsonify({"ok": True, "message": f"Successfully generated {filename} for download.", "filename": filename, "content": content})
    except Exception as e:
        session_db.rollback()
        return jsonify({"error": f"Error generating IPC file: {str(e)}"}), 500
    finally:
        session_db.close()


@lc_ms_api_bp.route("/generate/purif", methods=["POST"])
def generate_purif():
    """ Expects JSON body OR Form-Data: eln_id, purif_no, purif_method, fractions """
    eln_id = ""
    purif_no_val = ""
    purif_method = ""
    fractions_arr = []

    # 1. Attempt strict JSON parsing
    if request.is_json or (request.data and request.data.strip().startswith(b"{")):
        try:
            data = request.get_json(force=True, silent=True) or {}
            eln_id = str(data.get("eln_id", "")).strip()
            purif_no_val = data.get("purif_no")
            purif_method = str(data.get("purif_method", "")).strip()
            fractions_arr = data.get("fractions", [])
        except Exception:
            pass

    # 2. Attempt legacy Form-Data parsing (purif.html standalone view)
    if not eln_id and request.form:
        eln_id = str(request.form.get("eln_id", "")).strip()
        purif_no_val = request.form.get("purif_no")
        purif_method = request.form.get("purif_method", "")
        frac_str = request.form.get("fractions", "")
        import re
        fractions_arr = [f.strip() for f in re.split(r'[,;\s]+', frac_str) if f.strip()]

    if not eln_id:
        return jsonify({"error": "ELN is required."}), 400
        
    if not isinstance(fractions_arr, list) or not fractions_arr:
        return jsonify({"error": "Please provide an array of fraction labels."}), 400

    session_db = SessionLocal()
    try:
        rec = session_db.query(ELN).get(eln_id)
        if not rec:
             return jsonify({"error": f"ELN {eln_id} is not in the database."}), 400

        chemist_username = rec.chemist.strip().lower()
        user_id = get_user_id(chemist_username)
        if not user_id:
             return jsonify({"error": f"Chemist '{chemist_username}' has no configured userID mapping."}), 400

        try:
            p_no = int(float(purif_no_val))
        except (ValueError, TypeError):
             return jsonify({"error": "Purification number must be an integer."}), 400

        import re
        def norm_token(tok: str) -> str:
            t = str(tok or "").strip()
            if t.lower().startswith("f"): t = t[1:]
            t = t.replace(" ", "")
            t = re.sub(r"[^0-9+-]", "", t)
            t = re.sub(r"([+-]){2,}", r"\1", t)
            return t

        tokens = [norm_token(t) for t in fractions_arr if norm_token(t)]
        if not tokens:
             return jsonify({"error": "Please provide at least one valid fraction value."}), 400

        valid_re = re.compile(r"^\d+(?:[+-]\d+)?$")
        errors = []
        parsed_fracs = []
        
        for t in tokens:
            if not valid_re.match(t):
                errors.append(f"Fraction '{t}' is not valid. Use numbers, optionally with '-' or '+'.")
                continue
                
            m = re.search(r"^\d+", t)
            frac_no = int(m.group()) if m else None
            
            if frac_no is None:
                errors.append(f"Fraction '{t}' does not start with a number.")
                continue
                
            existing = session_db.query(PurifMeasurement).filter_by(eln_id=eln_id, purif_no=p_no, fraction_no=frac_no).first()
            if existing:
                errors.append(f"Fraction '{t}' (first number {frac_no}) already recorded for Purif {p_no}.")
                continue
                
            parsed_fracs.append((t, frac_no))

        if errors:
            return jsonify({"error": "\n".join(errors)}), 400

        lines = []
        vial = 1
        for t, frac_no in parsed_fracs:
            session_db.add(PurifMeasurement(
                chemist_username=chemist_username,
                eln_id=eln_id,
                purif_no=p_no,
                fraction_no=frac_no,
                fraction_label=t,
                purif_method=purif_method if purif_method else None
            ))

            label = f"{eln_id}_PURIF_{p_no}_LCMS_F{t}"
            cols = [str(vial), label] + rec.product_chemforms()
            lines.append("\t".join(cols))
            vial += 1

        content = "\n".join(lines)
        filename = f"{user_id}_PURIF.txt"

        session_db.commit()

        return jsonify({"ok": True, "message": f"Successfully generated {filename} for download.", "filename": filename, "content": content})
    except Exception as e:
        session_db.rollback()
        return jsonify({"error": f"Error generating Purification file: {str(e)}"}), 500
    finally:
        session_db.close()


@lc_ms_api_bp.route("/generate/products", methods=["POST"])
def generate_products():
    """ Expects JSON body: {"eln_id": "...", "product_numbers": [1, 2, 4]} """
    data = request.get_json(force=True, silent=True) or {}
    eln_id = str(data.get("eln_id", "")).strip()
    prod_list = data.get("product_numbers", [])
    
    if not eln_id:
        return jsonify({"error": "ELN is required."}), 400
        
    if not isinstance(prod_list, list) or not prod_list:
        return jsonify({"error": "Please provide an array of product numbers."}), 400
    
    session_db = SessionLocal()
    try:
        rec = session_db.query(ELN).get(eln_id)
        if not rec:
             return jsonify({"error": f"ELN {eln_id} is not in the database."}), 400
             
        chemist_username = rec.chemist.strip().lower()
        user_id = get_user_id(chemist_username)
        if not user_id:
             return jsonify({"error": f"Chemist '{chemist_username}' has no configured userID mapping."}), 400

        lines = []
        vial = 1
        p_forms = rec.product_chemforms()
        errors = []
        
        for p in prod_list:
            try:
                p_i = int(float(p))
            except (ValueError, TypeError):
                errors.append(f"Product number '{p}' is not a valid integer.")
                continue
                
            label = f"{eln_id}_P{p_i}"
            cols = [str(vial), label] + p_forms
            lines.append("\t".join(cols))
            vial += 1
            
        if errors:
            return jsonify({"error": "\n".join(errors)}), 400

        content = "\n".join(lines)
        filename = f"{user_id}_PRODUCTS.txt"

        # Products generator does not save new DB rows, just file generation
        return jsonify({"ok": True, "message": f"Successfully generated {filename} for download.", "filename": filename, "content": content})
    except Exception as e:
        return jsonify({"error": f"Error generating Products file: {str(e)}"}), 500
    finally:
        session_db.close()


# -------- Measurements queries APIs -------- #
@lc_ms_api_bp.route("/ipc-measurements")
def ipc_measurements():
    session_db = SessionLocal()
    try:
        q = request.args.get("q", "").strip()
        limit_arg = request.args.get("limit")
        offset_arg = request.args.get("offset", "0")
        
        query = session_db.query(IPCMeasurement).order_by(IPCMeasurement.id.desc())
        
        if q:
            like = f"%{q.lower()}%"
            query = query.filter(func.lower(IPCMeasurement.eln_id).like(like))
            
        total = query.count()
            
        if limit_arg:
            try:
                limit = int(limit_arg)
                offset = int(offset_arg)
                query = query.limit(limit).offset(offset)
            except ValueError:
                pass
                
        results = []
        for m in query.all():
            results.append({
                "id": m.id,
                "chemist_username": m.chemist_username,
                "eln_id": m.eln_id,
                "ipc_no": m.ipc_no,
                "duration_h": m.duration_h,
                "lc_ms_method_min": m.lc_ms_method_min,
                "lc_ms_instrument": m.lc_ms_instrument,
                "lc_ms_file_name": m.lc_ms_file_name,
                "ipc_result": m.ipc_result
            })
            
        return jsonify({"items": results, "total": total})
    finally:
        session_db.close()

@lc_ms_api_bp.route("/purif-measurements")
def purif_measurements():
    session_db = SessionLocal()
    try:
        q = request.args.get("q", "").strip()
        limit_arg = request.args.get("limit")
        offset_arg = request.args.get("offset", "0")
        
        query = session_db.query(PurifMeasurement).order_by(PurifMeasurement.id.desc())
        
        if q:
            like = f"%{q.lower()}%"
            query = query.filter(func.lower(PurifMeasurement.eln_id).like(like))
            
        total = query.count()
            
        if limit_arg:
            try:
                limit = int(limit_arg)
                offset = int(offset_arg)
                query = query.limit(limit).offset(offset)
            except ValueError:
                pass
                
        results = []
        for m in query.all():
            results.append({
                "id": m.id,
                "chemist_username": m.chemist_username,
                "eln_id": m.eln_id,
                "purif_no": m.purif_no,
                "fraction_no": m.fraction_no,
                "fraction_label": m.fraction_label,
                "purif_method": m.purif_method,
                "analysis_instrument": m.analysis_instrument,
                "analysis_file_name": m.analysis_file_name,
                "purif_result": m.purif_result
            })
            
        return jsonify({"items": results, "total": total})
    finally:
        session_db.close()

@lc_ms_api_bp.route("/ipc-measurements/<int:mid>", methods=["PATCH"])
def api_update_ipc(mid: int):
    session_db = SessionLocal()
    try:
        m = session_db.query(IPCMeasurement).get(mid)
        if not m:
            return jsonify({"error": "Not found"}), 404
        data = request.get_json(force=True, silent=True) or {}
        for key in ["lc_ms_method_min", "lc_ms_instrument", "lc_ms_file_name", "ipc_result"]:
            if key in data:
                setattr(m, key, (data[key] or "").strip())
        session_db.commit()
        return jsonify({"ok": True})
    finally:
        session_db.close()

@lc_ms_api_bp.route("/purif-measurements/<int:mid>", methods=["PATCH"])
def api_update_purif(mid: int):
    session_db = SessionLocal()
    try:
        m = session_db.query(PurifMeasurement).get(mid)
        if not m:
            return jsonify({"error": "Not found"}), 404
        data = request.get_json(force=True, silent=True) or {}
        for key in ["analysis_instrument", "analysis_file_name", "purif_result"]:
            if key in data:
                setattr(m, key, (data[key] or "").strip())
        session_db.commit()
        return jsonify({"ok": True})
    finally:
        session_db.close()
