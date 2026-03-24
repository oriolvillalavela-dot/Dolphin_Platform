
import os
import sys
import pandas as pd
from flask import Blueprint, request, jsonify, current_app
from sqlalchemy import func
from datetime import datetime

# Add parent directory to sys.path to allow importing LCMS_Analysis_Tool
# Assuming dolphin_platform is at root/dolphin_platform and LCMS is at root/LCMS_Analysis_Tool
lcms_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../"))
sys.path.append(lcms_path)
print(f"DEBUG: Added {lcms_path} to sys.path. Contents: {os.listdir(lcms_path) if os.path.exists(lcms_path) else 'PATH NOT FOUND'}")

try:
    from LCMS_Analysis_Tool.app.parsing.lcms_parser import parse_rpt
    from LCMS_Analysis_Tool.app.analysis import (
        build_raw_tables, link_peaks_exact, proximity_assign, auto_assign_roles_per_sample
    )
    from LCMS_Analysis_Tool.app.utils import mw_from_formula
except ImportError as e:
    print(f"Warning: Could not import LCMS_Analysis_Tool modules: {e}")
    # Define dummy functions to prevent NameError at module level, 
    # but raise error when called.
    def parse_rpt(*args, **kwargs): raise ImportError("LCMS_Analysis_Tool not found")
    def link_peaks_exact(*args, **kwargs): raise ImportError("LCMS_Analysis_Tool not found")
    def proximity_assign(*args, **kwargs): raise ImportError("LCMS_Analysis_Tool not found")
    def auto_assign_roles_per_sample(*args, **kwargs): raise ImportError("LCMS_Analysis_Tool not found")


from database import SessionLocal
from models import Batch, Chemical, QCResult

lcms_qc_bp = Blueprint('lcms_qc', __name__)

def run_qc_analysis(rpt_content, chemform):
    """
    Runs the QC analysis logic reusing LCMS_Analysis_Tool functions.
    Returns a dict with results.
    """
    # 1. Parse RPT
    sample_df, peak_df, mass_df = parse_rpt(rpt_content)
    
    if sample_df.empty:
        return {"error": "No samples found in RPT file"}

    # 2. Build sample_role_map
    # We assume we are looking for 'chemform' as 'Prod' in ALL samples found in the RPT
    # (or we could pick the first one)
    sample_ids = sample_df["sample_id"].unique()
    sample_role_map = {}
    for sid in sample_ids:
        sample_role_map[sid] = [(chemform, "Prod")]

    # 3. Build raw tables (needed for some internal logic? actually analysis functions take dfs directly)
    # raw = build_raw_tables(sample_df, peak_df, mass_df)

    # 4. Link peaks
    tmerge = link_peaks_exact(peak_df, mass_df)
    tmerge = proximity_assign(tmerge, peak_df, mass_df)

    # 5. Assign roles
    # This adds 'role', 'confidence_score', 'found_adduct', etc.
    final_df = auto_assign_roles_per_sample(tmerge, sample_role_map)

    # 6. Interpret results
    # We look for the BEST 'Prod' match across the samples (or just the first sample?)
    # Let's aggregate results per sample and pick the best one (highest purity/confidence)
    
    best_result = {
        "chem_found": "NO",
        "found_mass": None,
        "retention_time": None,
        "purity": "impure",
        "purity_percent": 0.0,
        "log": []
    }

    for sid in sample_ids:
        sp = final_df[final_df["Sample-ID"] == sid].copy()
        if sp.empty:
            continue

        # Calculate total area
        total_area = sp["peak_area"].sum()
        
        # Find Prod rows
        prod_rows = sp[sp["role"] == "Prod"]
        
        if not prod_rows.empty:
            # Pick best Prod by confidence then area
            prod_rows = prod_rows.sort_values(["confidence_score", "peak_area"], ascending=[False, False])
            best_prod = prod_rows.iloc[0]
            
            conf = best_prod.get("confidence_score", 0)
            if conf > 50: # Threshold for "Found"
                area_prod = best_prod["peak_area"]
                purity_pct = (area_prod / total_area * 100) if total_area > 0 else 0
                
                # Determine "Found Mass" - try to get it from the matched masses if possible
                # The 'masses' column contains a list of dicts. We can pick the top one.
                found_mass = None
                if isinstance(best_prod.get("masses"), list) and best_prod["masses"]:
                    found_mass = best_prod["masses"][0].get("mass")
                
                # Update best_result if this sample looks better
                if purity_pct > best_result["purity_percent"]:
                     best_result["chem_found"] = "YES"
                     best_result["found_mass"] = found_mass
                     best_result["retention_time"] = best_prod["rt_min"]
                     best_result["purity_percent"] = purity_pct
                     best_result["purity"] = "pure" if purity_pct > 85 else "impure" # Threshold 85%

    return best_result

@lcms_qc_bp.post('/batches/<batch_id>/qc_analysis')
def qc_analysis(batch_id):
    session = SessionLocal()
    try:
        # 1. Get Batch and Chemical
        batch = session.query(Batch).filter(Batch.batch_id == batch_id).first()
        if not batch:
            return jsonify({"ok": False, "error": "Batch not found"}), 404
        
        chem = session.query(Chemical).filter(Chemical.chem_id == batch.chem_id).first()
        if not chem or not chem.chemform:
            return jsonify({"ok": False, "error": "Chemical or Molecular Formula not found"}), 400

        # 2. Get File
        if 'file' not in request.files:
            return jsonify({"ok": False, "error": "No file uploaded"}), 400
        
        file = request.files['file']
        if not file.filename.endswith('.rpt'):
             return jsonify({"ok": False, "error": "Invalid file type. Only .rpt allowed"}), 400

        content = file.read().decode('utf-8', errors='ignore')

        # 3. Run Analysis
        res = run_qc_analysis(content, chem.chemform)
        
        if "error" in res:
             return jsonify({"ok": False, "error": res["error"]}), 500

        # 4. Save Result
        qc_res = QCResult(
            batch_id=batch_id,
            chem_found=res["chem_found"],
            found_mass=res["found_mass"],
            retention_time=res["retention_time"],
            purity=res["purity"],
            purity_percent=res["purity_percent"],
            filename=file.filename,
            analysis_log=f"Analyzed against formula {chem.chemform}"
        )
        session.add(qc_res)
        session.commit()

        return jsonify({"ok": True, "result": {
            "chem_found": res["chem_found"],
            "found_mass": res["found_mass"],
            "retention_time": res["retention_time"],
            "purity": res["purity"],
            "purity_percent": res["purity_percent"]
        }})

    except Exception as e:
        session.rollback()
        current_app.logger.exception("QC Analysis failed")
        return jsonify({"ok": False, "error": str(e)}), 500
    finally:
        session.close()

@lcms_qc_bp.get('/batches/<batch_id>/qc_results')
def get_qc_results(batch_id):
    session = SessionLocal()
    try:
        # Get latest result
        res = session.query(QCResult).filter(QCResult.batch_id == batch_id).order_by(QCResult.created_at.desc()).first()
        if not res:
            return jsonify({"ok": False, "error": "No results found"}), 404
            
        return jsonify({"ok": True, "item": {
            "chem_found": res.chem_found,
            "found_mass": res.found_mass,
            "retention_time": res.retention_time,
            "purity": res.purity,
            "purity_percent": res.purity_percent,
            "created_at": res.created_at.isoformat() if res.created_at else None,
            "filename": res.filename
        }})
    finally:
        session.close()
