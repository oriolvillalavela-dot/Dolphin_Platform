"""Shared pytest fixtures for Dolphin Platform tests."""

from __future__ import annotations

import pytest


# ── Sample SMILES Strings ──────────────────────────────────────────

@pytest.fixture
def aspirin_smiles() -> str:
    """Aspirin (acetylsalicylic acid) SMILES."""
    return "CC(=O)Oc1ccccc1C(=O)O"


@pytest.fixture
def ethanol_smiles() -> str:
    """Ethanol SMILES."""
    return "CCO"


@pytest.fixture
def benzene_smiles() -> str:
    """Benzene SMILES."""
    return "c1ccccc1"


@pytest.fixture
def invalid_smiles() -> str:
    """An intentionally invalid SMILES string."""
    return "not_a_molecule_XYZ!!!"


@pytest.fixture
def caffeine_smiles() -> str:
    """Caffeine SMILES."""
    return "Cn1c(=O)c2c(ncn2C)n(C)c1=O"


@pytest.fixture
def aniline_smiles() -> str:
    """Aniline (aminobenzene) SMILES."""
    return "Nc1ccccc1"
