<div align="center">

# 🐬 Dolphin Platform V2

**AI-Augmented Chemical Research Management**

[![CI](https://github.com/oriolvillalavela/dolphin-platform/actions/workflows/ci.yml/badge.svg)](https://github.com/oriolvillalavela/dolphin-platform/actions)
[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)
[![RDKit](https://img.shields.io/badge/RDKit-2023.09-orange.svg)](https://www.rdkit.org/)
[![Code style: ruff](https://img.shields.io/badge/code%20style-ruff-000000.svg)](https://github.com/astral-sh/ruff)

*A full-stack platform bridging cheminformatics, large language models, and laboratory automation for accelerated chemical discovery.*

</div>

---

## Abstract

Modern pharmaceutical research demands tight integration between **chemical inventory management**, **high-throughput experimentation (HTE)**, and **analytical data pipelines**. Dolphin Platform V2 addresses this by providing a unified web-based system that combines:

1. **Structured chemical data management** — SMILES/InChI parsing, functional group detection via SMARTS substructure matching, and CAS Registry lookups powered by [RDKit](https://www.rdkit.org/).
2. **LLM-augmented molecular intelligence** — Optical Chemical Structure Recognition (OCSR) via [MolScribe](https://github.com/thomas-young-2013/MolScribe), with AI fallback through the [Portkey](https://portkey.ai/) gateway for SMILES prediction and retrosynthetic reasoning.
3. **Autonomous experiment design** — AI-driven plate layout generation for high-throughput screenings, integrating ELN parsing with intelligent reagent assignment.
4. **End-to-end LC-MS analytics** — Automated IPC measurements, purification tracking, and quality control pipelines with direct instrument file access via SMB protocol.

This architecture reflects the paradigm shift toward **autonomous chemical agents** (cf. [ChemCrow](https://arxiv.org/abs/2304.05376), Boiko *et al.* 2023) and **NLP-driven synthesis planning** (Schwaller *et al.*, 2019), embedding LLM reasoning directly into laboratory workflows rather than treating AI as a disconnected tool.

---

## Architecture

```mermaid
graph TB
    subgraph Frontend["🖥️ Frontend — Jinja2 + JavaScript"]
        UI[Web Interface]
        PD[Plate Designer]
        SC[Screening Dashboard]
    end

    subgraph Core["⚙️ Core — Flask Application"]
        APP["app.py<br/>~40+ REST Endpoints"]
        MOD[models.py<br/>16 SQLAlchemy Models]
        DB[(PostgreSQL)]
    end

    subgraph Blueprints["🔌 Blueprint Modules"]
        LCMS["LC-MS Module<br/>IPC · Purification · QC"]
        PPM["PPM Module<br/>PDF → MolScribe OCR → SMILES"]
        SCRN["Screenings Module<br/>ELN Parsing · AI Plate Layout"]
    end

    subgraph Intelligence["🧠 AI & Cheminformatics"]
        RDKIT["RDKit Engine<br/>SMARTS · SVG · InChI"]
        MOLSCR["MolScribe<br/>Optical Structure Recognition"]
        LLM["LLM Gateway<br/>Portkey AI · Retrosynthesis"]
        CAS["CAS Registry API"]
    end

    subgraph External["📡 External Services"]
        SMB["SMB Protocol<br/>Instrument Files"]
        ELN["ELN System"]
    end

    UI --> APP
    PD --> APP
    SC --> SCRN

    APP --> MOD --> DB
    APP --> LCMS
    APP --> PPM
    APP --> SCRN

    LCMS --> SMB
    PPM --> MOLSCR
    PPM --> LLM
    SCRN --> LLM
    SCRN --> ELN

    APP --> RDKIT
    APP --> CAS

    style Frontend fill:#1a1a2e,stroke:#e94560,color:#fff
    style Core fill:#16213e,stroke:#0f3460,color:#fff
    style Blueprints fill:#0f3460,stroke:#533483,color:#fff
    style Intelligence fill:#533483,stroke:#e94560,color:#fff
    style External fill:#2c2c54,stroke:#474787,color:#fff
```

---

## Directory Structure

```
dolphin_platform/
├── app.py                          # Main Flask application (~40+ endpoints)
├── models.py                       # SQLAlchemy ORM (16 models)
├── database.py                     # PostgreSQL engine & session factory
├── Dockerfile                      # Multi-stage production build
├── pyproject.toml                  # PEP 621 project metadata
├── requirements.txt                # Pinned pip dependencies
├── environment.yml                 # Conda environment specification
│
├── blueprints/                     # Modular Flask blueprints
│   ├── lc_ms/                      #   LC-MS: IPC, purification, products
│   │   ├── api.py                  #     REST endpoints
│   │   ├── routes.py               #     Page rendering
│   │   └── utils.py                #     SMB file access, data parsing
│   ├── ppm/                        #   Project Process Management
│   │   ├── api.py                  #     REST endpoints
│   │   ├── extractor.py            #     PDF → region detection pipeline
│   │   ├── molscribe_runner.py     #     MolScribe OCSR integration
│   │   ├── ai_fallback.py          #     LLM-based SMILES prediction
│   │   └── normalization.py        #     Chemical name normalization
│   └── screenings/                 #   High-Throughput Screening
│       ├── api.py                  #     REST endpoints (plate CRUD, ELN)
│       ├── ai_layout.py            #     LLM-driven plate layout generation
│       └── lcms_backend.py         #     LC-MS integration for screenings
│
├── utils/                          # Shared cheminformatics utilities
│   ├── chem_utils.py               #   RDKit: SMARTS matching, SVG, PDF export
│   └── chem_converter/             #   CAS/IUPAC/SMILES/InChI converters
│
├── src/dolphin/agents/             # Showcase: typed, modular agent code
│   └── synthesis_agent.py          #   LLM retrosynthesis reasoning agent
│
├── tests/                          # pytest test suite
│   ├── conftest.py                 #   Shared fixtures
│   └── test_chem_utils.py          #   Unit tests for chem_utils
│
├── notebooks/                      # Research demonstration notebooks
│   └── demo.ipynb                  #   Interactive capability showcase
│
├── templates/                      # Jinja2 HTML templates
├── static/                         # CSS, JavaScript, icons
├── data/                           # Sample datasets (CSV/XLSX)
├── LCMS_Analysis_Tool/             # Standalone Streamlit LCMS app
└── .github/workflows/ci.yml       # GitHub Actions CI pipeline
```

---

## Installation

### Option 1: pip (recommended for development)

```bash
# Clone the repository
git clone https://github.com/oriolvillalavela/dolphin-platform.git
cd dolphin-platform

# Create virtual environment
python -m venv .venv
source .venv/bin/activate    # Linux/macOS
.venv\Scripts\activate       # Windows

# Install with all extras
pip install -e ".[dev,test,notebook]"
```

### Option 2: Conda (recommended for RDKit)

```bash
conda env create -f environment.yml
conda activate dolphin
```

### Option 3: Docker (production)

```bash
docker build -t dolphin-platform .
docker run --rm -p 8000:8000 \
  -e DB_NAME=dolphin \
  -e DB_HOST=host.docker.internal \
  -e DB_PORT=5432 \
  -e DB_USER=postgres \
  -e DB_PASSWORD=changeme \
  dolphin-platform
```

### Environment Variables

Create a `.env` file in the project root:

```env
DB_NAME=dolphin
DB_HOST=localhost
DB_PORT=5432
DB_USER=postgres
DB_PASSWORD=your_password

# External services (optional)
CAS_API_KEY=your_cas_api_key
PORTKEY_API_KEY=your_portkey_key
```

---

## Quick Start

### 1. Chemical Structure Processing

```python
from utils.chem_utils import compute_functional_groups, generate_structure_svg

# Aspirin (acetylsalicylic acid)
smiles = "CC(=O)Oc1ccccc1C(=O)O"

# Detect functional groups via SMARTS substructure matching
groups = compute_functional_groups(smiles)
print(groups)
# ['aromatic_ring', 'carboxylic_acid', 'ester']

# Generate publication-quality SVG
svg = generate_structure_svg(smiles, width=400, height=400)
with open("aspirin.svg", "w") as f:
    f.write(svg)
```

### 2. RDKit Molecular Descriptors

```python
from rdkit import Chem
from rdkit.Chem import Descriptors

mol = Chem.MolFromSmiles("CC(=O)Oc1ccccc1C(=O)O")

print(f"Molecular Weight:  {Descriptors.MolWt(mol):.2f}")
print(f"LogP:              {Descriptors.MolLogP(mol):.2f}")
print(f"H-Bond Donors:     {Descriptors.NumHDonors(mol)}")
print(f"H-Bond Acceptors:  {Descriptors.NumHAcceptors(mol)}")
print(f"Rotatable Bonds:   {Descriptors.NumRotatableBonds(mol)}")
# Molecular Weight:  180.16
# LogP:              1.31
# H-Bond Donors:     1
# H-Bond Acceptors:  4
# Rotatable Bonds:   3
```

### 3. AI-Driven Synthesis Agent (Showcase)

```python
from src.dolphin.agents.synthesis_agent import SynthesisAgent, MoleculeDescriptor

# Initialize the agent with your LLM API key
agent = SynthesisAgent(api_key="your-portkey-key")

# Describe a target molecule
target = MoleculeDescriptor.from_smiles("CC(=O)Oc1ccccc1C(=O)O")
print(f"Target: {target.name} | MW: {target.molecular_weight:.1f}")
print(f"Functional groups: {target.functional_groups}")

# Request retrosynthetic analysis
result = agent.propose_retrosynthesis(
    target_smiles=target.smiles,
    strategy="single_step",
    max_proposals=3,
)
for i, route in enumerate(result.routes, 1):
    print(f"Route {i}: {route.precursors} → {route.target}")
```

---

## State of the Art

Dolphin Platform V2 builds upon and complements several key advances at the intersection of AI and chemistry:

| Domain | Foundational Work | How Dolphin Extends It |
|--------|-------------------|----------------------|
| **Molecular Transformers** | Schwaller *et al.* (2019) — *Molecular Transformer* for reaction prediction | Integrates transformer-based reasoning via LLM gateway for retrosynthetic proposals within laboratory workflows |
| **Chemical Language Models** | Weininger (1988) — SMILES; Schwaller *et al.* (2021) — *Mapping chemical reactions with attention* | Uses SMILES as the canonical molecular representation across all modules; SMARTS-based functional group detection |
| **Autonomous Agents** | Boiko *et al.* (2023) — *ChemCrow*; Bran *et al.* (2024) | PPM module implements agent-like PDF → OCR → SMILES pipeline with AI fallback; Screening module uses LLM for autonomous plate layout |
| **OCSR** | Xu *et al.* (2022) — *MolScribe* | Direct integration of MolScribe for extracting molecular structures from research PDF documents |
| **HTE Digitization** | Perera *et al.* (2018) — *Platform for nanomole-scale synthesis* | Digital twin of HTE plates: 24/96-well design, reagent assignment, and LC-MS result integration |

> **Key Differentiator**: While most AI-chemistry tools operate as standalone models, Dolphin Platform embeds AI capabilities *within the laboratory management workflow itself* — from PDF intake to plate design to analytical result tracking — creating a closed-loop system for accelerated discovery.

---

## Testing

```bash
# Run all tests
pytest

# With coverage report
pytest --cov=utils --cov=src --cov-report=html

# Run only fast unit tests
pytest -m "not slow"
```

---

## Contributing

We welcome contributions. Please follow these guidelines:

1. **Fork** the repository and create a feature branch
2. **Install** dev dependencies: `pip install -e ".[dev,test]"`
3. **Lint** your code: `ruff check . --fix`
4. **Type-check**: `mypy src/`
5. **Test**: `pytest`
6. **Submit** a pull request with a clear description

---

## Citation

If you use Dolphin Platform in your research, please cite:

```bibtex
@software{villalba2026dolphin,
  author  = {Villalba Vela, Oriol},
  title   = {Dolphin Platform V2: AI-Augmented Chemical Research Management},
  year    = {2026},
  url     = {https://github.com/oriolvillalavela/dolphin-platform},
  note    = {Flask + RDKit + LLM integration for HTE, LC-MS, and molecular intelligence},
}
```

---

## License

This project is licensed under the MIT License — see the [LICENSE](LICENSE) file for details.

---

<div align="center">

*Built with ❤️ for the chemical sciences community*

**[Roche](https://www.roche.com/) · [EPFL](https://www.epfl.ch/) · [RDKit](https://www.rdkit.org/)**

</div>
