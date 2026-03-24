# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Dolphin Platform V2 is a chemical research management system built with Flask + PostgreSQL. It manages chemical inventories, laboratory experiments, high-throughput screenings, LC-MS analysis, and project process tracking, with heavy chemistry informatics integration (RDKit, CAS Registry, MolScribe).

## Running the Application

**Docker (Production):**
```bash
docker build -t chemreg_v9 .
docker run --rm -p 8000:8000 \
  -e DB_NAME=$DB_NAME -e DB_HOST=$DB_HOST -e DB_PORT=$DB_PORT \
  -e DB_USER=$DB_USER -e DB_PASSWORD=$DB_PASSWORD \
  chemreg_v9
```

**LCMS Analysis Tool (Streamlit, separate app):**
```bash
pip install -r LCMS_Analysis_Tool/requirements.txt
streamlit run LCMS_Analysis_Tool/app/main.py
# Or via Docker: docker build -t lcms-app LCMS_Analysis_Tool/ && docker run -p 8011:8080 -e PORT=8080 lcms-app
```

## Running Tests

Tests are standalone scripts at the project root:
```bash
python test_analysis_tool.py
python test_api_search.py
python test_lcms_api.py
python test_plate_designer.py
python test_rxn.py
python test_screenings_eln_parser.py
```

## Architecture

### Backend Structure

- **`app.py`** — Main Flask app (~2,800 lines). Contains ~40+ REST endpoints for core inventory (chemicals, bottles, batches, plates), schema migration logic, and database initialization.
- **`models.py`** — SQLAlchemy ORM models: `Chemical`, `Bottle`, `Batch`, `Plate`, `PlateWellReagent`, `Surf`, and others.
- **`database.py`** — PostgreSQL connection setup via SQLAlchemy.

### Blueprint Modules (`blueprints/`)

Each module follows the pattern: `routes.py` (page rendering) + `api.py` (REST endpoints) + helper modules.

| Module | Path | Purpose |
|--------|------|---------|
| **LC-MS** | `blueprints/lc_ms/` | Liquid chromatography-mass spectrometry: IPC, purification, products, measurements |
| **PPM** | `blueprints/ppm/` | Project Process Management: PDF upload, MolScribe OCR for molecule extraction, SMILES generation, AI fallback |
| **Screenings** | `blueprints/screenings/` | High-throughput screening: ELN parsing, AI plate layout generation, LC-MS integration |
| **LC-MS QC** | `blueprints/lcms_qc.py` | Quality control analysis |
| **LC-MS Analyser** | `blueprints/lcms_analyser.py` | Analysis pipeline |

### Frontend

- **`static/app.js`** (~2,355 lines) — Core UI: chemical search, bottle/batch management, plate designer interactions.
- **`static/analysis_tool.js`**, **`batch_ui.js`**, **`plate.js`** — Module-specific logic.
- Templates use Jinja2 server-side rendering with `templates/base.html` as the base.

### Utilities (`utils/`)

- **`chem_utils.py`** — RDKit-based structure processing, SMARTS functional group detection, SVG generation.
- **`chem_converter/cas_client.py`** — CAS Registry API integration for chemical lookups.
- **`chem_converter/converters.py`** — IUPAC/SMILES/InChI interconversion.

### External Integrations

- **CAS Registry** — Chemical lookup via credentials in `.env`
- **Portkey AI** — LLM services (used in PPM for AI fallback and screenings for layout generation)
- **MolScribe** — OCR model for extracting molecular structures from PDF documents (used in PPM)
- **SMB protocol** (`smbprotocol`) — Network file access for LCMS data files

### LCMS Analysis Tool (`LCMS_Analysis_Tool/`)

A standalone Streamlit application (separate from the main Flask app) for LCMS data analysis. Has its own `requirements.txt` and Dockerfile. Entry point: `LCMS_Analysis_Tool/app/main.py`.

## Key Patterns

- **Schema migration**: `app.py` runs `ALTER TABLE` migrations on startup to handle evolving schema without full migrations framework.
- **Override system**: `overrides/templates/` and `overrides/static/` can replace default templates/static files, loaded at startup.
- **Health check**: `GET /health` endpoint used by Docker.
- **Database**: PostgreSQL required — no SQLite fallback. Connection configured via `DB_NAME`, `DB_HOST`, `DB_PORT`, `DB_USER`, `DB_PASSWORD` env vars.
- **Chemistry operations**: Always use RDKit via `utils/chem_utils.py` rather than direct RDKit imports elsewhere.
