\
import io, os, tempfile, re, pandas as pd
from typing import Optional
try:
    from rdkit.Chem.rdchem import GetPeriodicTable
    _PT = GetPeriodicTable()
except Exception:
    _PT = None
try:
    from molmass import Formula
except ImportError:
    Formula = None

def ensure_tmp_session_dir() -> str:
    base = tempfile.gettempdir()
    path = os.path.join(base, f"lcms_app_{os.getpid()}")
    os.makedirs(path, exist_ok=True)
    return path

def cleanup_tmp_session_dir(path: str):
    try:
        if path and os.path.isdir(path):
            for name in os.listdir(path):
                p = os.path.join(path, name)
                try:
                    os.remove(p)
                except Exception:
                    pass
            os.rmdir(path)
    except Exception:
        pass

def parse_tsv_mapping(file_bytes: bytes) -> pd.DataFrame:
    text = file_bytes.decode("utf-8", errors="ignore")
    first_line = text.splitlines()[0] if text.splitlines() else ""
    # Detect delimiter by counts
    tab_count = first_line.count("\t")
    comma_count = first_line.count(",")
    if tab_count >= comma_count and tab_count > 0:
        sep = "\t"
    elif comma_count > 0:
        sep = ","
    else:
        sep = "\t"
    df = pd.read_csv(io.StringIO(text), sep=sep, header=None)
    # Ensure at least position + sample_id
    while df.shape[1] < 2:
        df[df.shape[1]] = ""
    cols = ["position", "sample_id"]
    nchem = max(0, df.shape[1] - 2)
    cols += [f"chem{i}" for i in range(1, nchem+1)]
    df.columns = cols
    return df

_ELEM_RE = re.compile(r"([A-Z][a-z]?)(\d*(?:\.\d+)?)")
def mw_from_formula_rdkit(formula: str, monoisotopic: bool = True) -> Optional[float]:
    formula = (formula or "").strip()
    if not formula or _PT is None:
        return None
    try:
        total = 0.0
        for elem, count in _ELEM_RE.findall(formula):
            n = float(count) if count else 1.0
            z = _PT.GetAtomicNumber(elem)
            if z <= 0:
                return None
            mass = _PT.GetMostCommonIsotopeMass(z) if monoisotopic else _PT.GetAtomicWeight(z)
            total += mass * n
        return total
    except Exception:
        return None

def mw_from_formula(formula: str) -> Optional[float]:
    val = mw_from_formula_rdkit(formula, monoisotopic=True)
    if val is not None:
        return val
    if Formula is None:
        return None
    try:
        return float(Formula(formula).isotope.mass)
    except Exception:
        try:
            return float(Formula(formula).mass)
        except Exception:
            return None
