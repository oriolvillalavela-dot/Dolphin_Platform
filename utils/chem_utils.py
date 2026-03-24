import io
from rdkit import Chem
from rdkit.Chem import Draw
from rdkit.Chem.Draw import rdMolDraw2D
import matplotlib.pyplot as plt
from matplotlib.backends.backend_pdf import PdfPages
import pandas as pd

# Define functional group SMARTS patterns
# Define functional group SMARTS patterns
FUNCTIONAL_GROUPS = {
  # Oxygen-containing
  "alcohol": [
    "[OX2H][CX4]"                 # ROH (excludes acids/phenols by carbon type)
  ],
  "phenol": [
    "[OX2H]c"                      # ArOH
  ],
  "ether": [
    "[OD2]([#6;!$([CX3](=O))])[#6;!$([CX3](=O))]"  # R-O-R, excludes ester-like acyl O
  ],
  "epoxide": [
    "[OX2r3]1[#6r3][#6r3]1"        # 3-membered cyclic ether
  ],
  "aldehyde": [
    "[CX3H1](=O)[#6]"              # R-CH=O
  ],
  "ketone": [
    "[#6][CX3](=O)[#6]"            # R-CO-R
  ],
  "carboxylic_acid": [
    "[CX3](=O)[OX2H1]"             # R-CO2H
  ],
  "carboxylate": [
    "[CX3](=O)[O-]"                # R-CO2-
  ],
  "ester": [
    "[CX3](=O)[OX2H0][#6]"         # R-CO2R
  ],
  "anhydride": [
    "[CX3](=O)O[CX3](=O)"          # R-CO-O-CO-R
  ],
  "acyl_halide": [
    "[CX3](=O)[F,Cl,Br,I]"         # R-COX
  ],
  "carbonate": [
    "[OX2][CX3](=O)[OX2]"          # RO-CO-OR
  ],
  "carbamate": [
    "[OX2][CX3](=O)[NX3]"          # RO-CO-NR2
  ],

  # Nitrogen-containing
  "amine_primary": [
    "[NX3;H2][#6]"                 # R-NH2
  ],
  "amine_secondary": [
    "[NX3;H1]([#6])[#6]"           # R2NH
  ],
  "amine_tertiary": [
    "[NX3;H0]([#6])([#6])[#6]"     # R3N
  ],
  "aniline_arylamine": [
    "[NX3;H2,H1]c"                 # Ar-NH2 / Ar-NHR
  ],
  "amide": [
    "[CX3](=O)[NX3]"               # R-CO-NR2
  ],
  "urea": [
    "[NX3][CX3](=O)[NX3]"          # NR-CO-NR
  ],
  "imide": [
    "[NX3]([CX3](=O))[CX3](=O)"    # N flanked by two carbonyls
  ],
  "imine": [
    "[CX3]=[NX2]"                  # C=N (broad; may overlap with amidines etc.)
  ],
  "oxime": [
    "[CX3]=[NX2][OX2H]"            # C=NOH
  ],
  "hydrazone": [
    "[CX3]=[NX2][NX3]"             # C=NN
  ],
  "azo": [
    "[NX2]=[NX2]"                  # N=N
  ],
  "nitrile": [
    "[CX2]#N"                      # C#N
  ],
  "nitro": [
    "[NX3+](=O)[O-]"               # common nitro representation
  ],
  "azide": [
    "[NX2]=[NX2+]=[NX1-]"          # -N3 (one common valence form)
  ],
  "guanidine": [
    "[NX3][CX3](=[NX2])[NX3]"      # N-C(=N)-N
  ],

  # Sulfur-containing
  "thiol": [
    "[SX2H][#6]"                   # R-SH
  ],
  "thioether_sulfide": [
    "[SX2]([#6])[#6]"              # R-S-R
  ],
  "disulfide": [
    "[SX2][SX2]"                   # R-S-S-R
  ],
  "thioester": [
    "[CX3](=O)[SX2][#6]"           # R-C(=O)-S-R
  ],
  "thioamide": [
    "[CX3](=S)[NX3]"               # R-C(=S)-N
  ],
  "sulfoxide": [
    "[SX3](=O)[#6]"                # R-S(=O)-R (broad)
  ],
  "sulfone": [
    "[SX4](=O)(=O)[#6]"            # R-S(=O)2- (broad)
  ],
  "sulfonamide": [
    "[SX4](=O)(=O)[NX3]"           # R-S(=O)2-NR2
  ],
  "thiol_sulfonic_acid": [
    "[SX4](=O)(=O)[OX2H]"          # R-SO3H (sulfonic acid)
  ],

  # Halogens (often useful filters)
  "alkyl_halide": [
    "[CX4][F,Cl,Br,I]"             # R-CH2-X / R-CH-X / R-C-X
  ],
  "aryl_halide": [
    "c[F,Cl,Br,I]"                 # Ar-X
  ],
  "fluoro": [
    "[#6][F]"
  ],
  "chloro": [
    "[#6][Cl]"
  ],
  "bromo": [
    "[#6][Br]"
  ],
  "iodo": [
    "[#6][I]"
  ],

  # Unsaturation / aromatic
  "alkene": [
    "[CX3]=[CX3]"                  # C=C
  ],
  "alkyne": [
    "[CX2]#[CX2]"                  # C#C
  ],
  "aromatic_ring": [
    "a1aaaaa1"                     # benzene-like ring (basic marker)
  ],

  # Phosphorus / boron (optional but common)
  "phosphate_or_phosphoric_acid": [
    "[PX4](=O)([OX2H0,OX2H1])([OX2H0,OX2H1])[OX2H0,OX2H1]"
  ],
  "boronic_acid": [
    "[BX3]([OX2H])[OX2H]"           # R-B(OH)2
  ],
}

FG_METADATA = {
    # Oxygen-containing
    "alcohol": "Oxygen-containing",
    "phenol": "Oxygen-containing",
    "ether": "Oxygen-containing",
    "epoxide": "Oxygen-containing",
    "aldehyde": "Oxygen-containing",
    "ketone": "Oxygen-containing",
    "carboxylic_acid": "Oxygen-containing",
    "carboxylate": "Oxygen-containing",
    "ester": "Oxygen-containing",
    "anhydride": "Oxygen-containing",
    "acyl_halide": "Oxygen-containing",
    "carbonate": "Oxygen-containing",
    "carbamate": "Oxygen-containing",

    # Nitrogen-containing
    "amine_primary": "Nitrogen-containing",
    "amine_secondary": "Nitrogen-containing",
    "amine_tertiary": "Nitrogen-containing",
    "aniline_arylamine": "Nitrogen-containing",
    "amide": "Nitrogen-containing",
    "urea": "Nitrogen-containing",
    "imide": "Nitrogen-containing",
    "imine": "Nitrogen-containing",
    "oxime": "Nitrogen-containing",
    "hydrazone": "Nitrogen-containing",
    "azo": "Nitrogen-containing",
    "nitrile": "Nitrogen-containing",
    "nitro": "Nitrogen-containing",
    "azide": "Nitrogen-containing",
    "guanidine": "Nitrogen-containing",

    # Sulfur-containing
    "thiol": "Sulfur-containing",
    "thioether_sulfide": "Sulfur-containing",
    "disulfide": "Sulfur-containing",
    "thioester": "Sulfur-containing",
    "thioamide": "Sulfur-containing",
    "sulfoxide": "Sulfur-containing",
    "sulfone": "Sulfur-containing",
    "sulfonamide": "Sulfur-containing",
    "thiol_sulfonic_acid": "Sulfur-containing",

    # Halogens
    "alkyl_halide": "Halogens",
    "aryl_halide": "Halogens",
    "fluoro": "Halogens",
    "chloro": "Halogens",
    "bromo": "Halogens",
    "iodo": "Halogens",

    # Unsaturation / aromatic
    "alkene": "Unsaturation / aromatic",
    "alkyne": "Unsaturation / aromatic",
    "aromatic_ring": "Unsaturation / aromatic",

    # Phosphorus / boron
    "phosphate_or_phosphoric_acid": "Phosphorus / boron",
    "boronic_acid": "Phosphorus / boron",
}

def compute_functional_groups(smiles):
    """
    Parses SMILES and detects functional groups.
    Returns a list of functional group names.
    """
    if not smiles:
        return []
    
    try:
        mol = Chem.MolFromSmiles(smiles)
        if not mol:
            return []
        
        groups = []
        for name, smarts_list in FUNCTIONAL_GROUPS.items():
            # Handle both list and single string (just in case, though we defined lists above)
            if isinstance(smarts_list, str):
                smarts_list = [smarts_list]
                
            for smarts in smarts_list:
                pattern = Chem.MolFromSmarts(smarts)
                if pattern and mol.HasSubstructMatch(pattern):
                    groups.append(name)
                    break # Found one match for this group name, move to next group
        
        return sorted(groups)
    except Exception:
        return []

def generate_structure_svg(smiles, width=300, height=300):
    """
    Generates an SVG string for the given SMILES.
    """
    if not smiles:
        return None
    
    try:
        mol = Chem.MolFromSmiles(smiles)
        if not mol:
            return None
        
        drawer = rdMolDraw2D.MolDraw2DSVG(width, height)
        drawer.DrawMolecule(mol)
        drawer.FinishDrawing()
        svg = drawer.GetDrawingText()
        return svg
    except Exception:
        return None

def generate_pdf_export(data_list):
    """
    Generates a PDF file from a list of dictionaries.
    Each dictionary should have keys: 'Chem_ID', 'SMILES', 'CAS', 'RO SRN'.
    Returns a bytes object containing the PDF data.
    """
    if not data_list:
        return b""

    buffer = io.BytesIO()
    
    # Create a DataFrame for easier handling
    df = pd.DataFrame(data_list)
    # Ensure columns are in order
    cols = ['Chem_ID', 'SMILES', 'CAS', 'RO SRN']
    # Add missing columns if any
    for c in cols:
        if c not in df.columns:
            df[c] = ""
    df = df[cols]

    # Use matplotlib to render a table
    with PdfPages(buffer) as pdf:
        # Estimate pages needed (approx 20 rows per page)
        rows_per_page = 20
        total_rows = len(df)
        
        for start_row in range(0, total_rows, rows_per_page):
            end_row = min(start_row + rows_per_page, total_rows)
            page_df = df.iloc[start_row:end_row]
            
            fig, ax = plt.subplots(figsize=(11.69, 8.27)) # A4 Landscape
            ax.axis('tight')
            ax.axis('off')
            
            # Create table
            table_data = [df.columns.values.tolist()] + page_df.values.tolist()
            table = ax.table(cellText=table_data, colLabels=None, loc='center', cellLoc='left')
            
            # Style
            table.auto_set_font_size(False)
            table.set_fontsize(10)
            table.scale(1, 1.5)
            
            # Adjust column widths manually if needed, or let auto
            # For SMILES, we might need more width, but let's trust matplotlib for now
            
            pdf.savefig(fig, bbox_inches='tight')
            plt.close(fig)
            
    buffer.seek(0)
    return buffer.getvalue()
