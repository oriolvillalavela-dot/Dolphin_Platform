# LC/MS Automation v2.1.3

New:
- Per-sample MW table (with numbered duplicate roles per sample).
- Dynamic number of chem columns & dropdowns.
- Product presence checks handle numbered roles (`Prod1`, `SM2`, ...).
- Safer joins for role display.

## Local
```
python -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\Activate.ps1
pip install -r requirements.txt
streamlit run app/main.py
```

## Docker
```
docker build -t lcms-app:2.1.3 .
docker run --rm -p 8011:8080 -e PORT=8080 lcms-app:2.1.3
```
