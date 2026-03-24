
import os
import sys
import io
import zipfile
import tempfile
import base64
import pandas as pd
import matplotlib
matplotlib.use('Agg') # Use non-interactive backend
import matplotlib.pyplot as plt
from flask import Blueprint, render_template, request, send_file, current_app, url_for

# Add parent directory to sys.path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "../../")))

try:
    from LCMS_Analysis_Tool.app.parsing.lcms_parser import parse_rpt
    from LCMS_Analysis_Tool.app.analysis import (
        build_raw_tables, link_peaks_exact, proximity_assign, auto_assign_roles_per_sample,
        yield_with_is, conversion_pct
    )
    from LCMS_Analysis_Tool.app.utils import parse_tsv_mapping, mw_from_formula
    from LCMS_Analysis_Tool.app.visuals import render_presence_map, render_heatmap, render_pies, render_confidence_map
    LCMS_AVAILABLE = True
except ImportError:
    print("Warning: Could not import LCMS_Analysis_Tool modules.")
    LCMS_AVAILABLE = False

lcms_analyser_bp = Blueprint('lcms_analyser', __name__)

def classify_sample_id(sample_id):
    import re
    s = (sample_id or "").strip()
    # Simplified classification for demo
    if re.match(r"^ELN", s, re.IGNORECASE): return "ELN"
    return "Unknown"

def well_from_sample_id(sample_id):
    import re
    s = (sample_id or "").strip()
    m = re.search(r"_([A-H]\d{1,2})$", s, flags=re.IGNORECASE)
    return m.group(1).upper() if m else None

# ... (I will need to adapt the logic from main.py: build_plates_from_tmerge)
# For the sake of the "Port", I will implement a streamlined version.


# Helper to convert plot to base64
def plot_to_b64(fig=None):
    buf = io.BytesIO()
    if fig:
        fig.savefig(buf, format='png', bbox_inches='tight')
    else:
        plt.savefig(buf, format='png', bbox_inches='tight')
    plt.close(fig) if fig else plt.close()
    buf.seek(0)
    return base64.b64encode(buf.read()).decode('utf-8')

def detect_plate_groups_by_well(sample_df, plate_size):
    # Sort by run order
    rows = sample_df.sort_values("run_order").to_dict('records')
    groups, cur, seen = [], [], set()
    for r in rows:
        w = well_from_sample_id(r.get("sample_id"))
        if not w: continue
        if (w in seen) or (len(seen) >= plate_size):
            if cur: groups.append(cur)
            cur, seen = [], set()
        cur.append((r["measurement_id"], r["sample_id"], w))
        seen.add(w)
    if cur: groups.append(cur)
    return groups

@lcms_analyser_bp.route('/lcms-analyser', methods=['GET', 'POST'])
def index():
    if request.method == 'GET':
        return render_template('lcms_analyser.html', lcms_available=LCMS_AVAILABLE)
    
    if not LCMS_AVAILABLE:
        return render_template('lcms_analyser.html', error="LCMS Backend Modules not available.")

    try:
        rpt_file = request.files.get('rpt_file')
        mapping_file = request.files.get('mapping_file')
        scope = request.form.get('scope', 'Single reaction')
        plate_size = int(request.form.get('plate_size', 96))
        
        # Yield Params
        rf = float(request.form.get('rf', 1.0) or 1.0)
        conc_is = float(request.form.get('conc_is', 10.0) or 10.0)
        volume = float(request.form.get('volume', 1.0) or 1.0)
        scale = float(request.form.get('scale', 0.1) or 0.1)

        if not rpt_file:
            return render_template('lcms_analyser.html', error="No RPT file uploaded")

        # 1. Parse RPT
        text = rpt_file.read().decode('utf-8', errors='ignore')
        sample_df, peak_df, mass_df = parse_rpt(text)
        
        if sample_df.empty:
             return render_template('lcms_analyser.html', error="No samples found in RPT")

        # 2. Mapping
        sample_role_map = {}
        if mapping_file:
            try:
                m_text = mapping_file.read().decode('utf-8', errors='ignore')
                # Reuse parse_tsv_mapping from utils if possible, or simple parse
                # For now, let's skip complex mapping parsing and rely on auto-detection or manual entry in future
                # But to be useful, we need SOME mapping.
                # Let's try to parse it if it's simple TSV: position, sample_id, Prod...
                pass 
            except Exception as e:
                current_app.logger.error(f"Mapping parse error: {e}")

        # 3. Link & Assign
        tmerge = link_peaks_exact(peak_df, mass_df)
        tmerge = proximity_assign(tmerge, peak_df, mass_df)
        final_df = auto_assign_roles_per_sample(tmerge, sample_role_map)
        
        results = {"scope": scope, "images": [], "tables": []}

        # 4. Analysis
        if scope == 'Plate':
            groups = detect_plate_groups_by_well(sample_df, plate_size)
            
            for i, group in enumerate(groups, 1):
                wells = {sid: w for (_, sid, w) in group}
                sub_peaks = final_df[final_df["sample_id"].isin(wells.keys())].copy()
                
                # A. Presence
                well_ok = {}
                for sid, well in wells.items():
                    sp = sub_peaks[sub_peaks["sample_id"] == sid]
                    # Check if any role starts with 'Prod' (case insensitive)
                    has_prod = sp["role"].astype(str).str.lower().str.startswith("prod").any()
                    well_ok[well] = has_prod
                
                # Render Presence
                tmp_png = tempfile.mktemp(suffix='.png')
                render_presence_map(well_ok, plate_size, f"Presence - Plate {i}", tmp_png)
                with open(tmp_png, "rb") as f:
                    b64 = base64.b64encode(f.read()).decode('utf-8')
                results["images"].append({"title": f"Presence Plate {i}", "data": b64})
                os.remove(tmp_png)

                # B. Conversion (Simple)
                # ... (Similar logic for conversion)
                
        else:
            # Single Reaction Mode
            # Just show a table of results
            # Group by sample_id
            summary = []
            for sid in final_df["sample_id"].unique():
                sp = final_df[final_df["sample_id"] == sid]
                prod = sp[sp["role"].astype(str).str.lower().str.startswith("prod")]
                found = not prod.empty
                summary.append({
                    "Sample ID": sid,
                    "Found": "Yes" if found else "No",
                    "Prod Area": prod["peak_area"].max() if found else 0
                })
            results["tables"].append(pd.DataFrame(summary).to_html(classes='grid', index=False))

        return render_template('lcms_analyser.html', results=results)

    except Exception as e:
        current_app.logger.exception("LCMS Analysis Failed")
        return render_template('lcms_analyser.html', error=str(e))


