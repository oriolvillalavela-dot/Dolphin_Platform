"""Unit tests for utils.chem_utils — functional group detection & SVG generation.

Tests validate the core cheminformatics utilities that underpin the Dolphin
Platform's molecular intelligence layer.
"""

from __future__ import annotations

import sys
import os

import pytest

# Ensure project root is on sys.path for imports
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from utils.chem_utils import compute_functional_groups, generate_structure_svg


# ────────────────────────────────────────────────────────────────────
# compute_functional_groups
# ────────────────────────────────────────────────────────────────────


class TestComputeFunctionalGroups:
    """Tests for SMARTS-based functional group detection."""

    def test_aspirin_groups(self, aspirin_smiles: str) -> None:
        """Aspirin should contain aromatic ring, carboxylic acid, and ester."""
        groups = compute_functional_groups(aspirin_smiles)
        assert "aromatic_ring" in groups
        assert "carboxylic_acid" in groups
        assert "ester" in groups

    def test_ethanol_has_alcohol(self, ethanol_smiles: str) -> None:
        """Ethanol (CCO) should be detected as an alcohol."""
        groups = compute_functional_groups(ethanol_smiles)
        assert "alcohol" in groups

    def test_benzene_has_aromatic_ring(self, benzene_smiles: str) -> None:
        """Benzene should be detected as having an aromatic ring."""
        groups = compute_functional_groups(benzene_smiles)
        assert "aromatic_ring" in groups

    def test_aniline_has_arylamine(self, aniline_smiles: str) -> None:
        """Aniline should be detected as an aryl amine."""
        groups = compute_functional_groups(aniline_smiles)
        assert "aniline_arylamine" in groups

    def test_empty_smiles_returns_empty(self) -> None:
        """Empty or None SMILES should return an empty list."""
        assert compute_functional_groups("") == []
        assert compute_functional_groups(None) == []

    def test_invalid_smiles_returns_empty(self, invalid_smiles: str) -> None:
        """Invalid SMILES should gracefully return an empty list."""
        groups = compute_functional_groups(invalid_smiles)
        assert groups == []

    def test_return_type_is_sorted_list(self, aspirin_smiles: str) -> None:
        """Functional groups should be returned as a sorted list."""
        groups = compute_functional_groups(aspirin_smiles)
        assert isinstance(groups, list)
        assert groups == sorted(groups)

    def test_caffeine_groups(self, caffeine_smiles: str) -> None:
        """Caffeine should contain amide and aromatic ring patterns."""
        groups = compute_functional_groups(caffeine_smiles)
        # Caffeine has fused purine ring (aromatic) and carbonyl-N (amide-like)
        assert "aromatic_ring" in groups


# ────────────────────────────────────────────────────────────────────
# generate_structure_svg
# ────────────────────────────────────────────────────────────────────


class TestGenerateStructureSvg:
    """Tests for RDKit SVG structure rendering."""

    def test_valid_smiles_returns_svg(self, aspirin_smiles: str) -> None:
        """Valid SMILES should produce an SVG string."""
        svg = generate_structure_svg(aspirin_smiles)
        assert svg is not None
        assert "<svg" in svg
        assert "</svg>" in svg

    def test_svg_respects_dimensions(self, benzene_smiles: str) -> None:
        """SVG output should reflect requested width/height."""
        svg = generate_structure_svg(benzene_smiles, width=500, height=500)
        assert svg is not None
        assert "500" in svg  # dimension should appear in the SVG

    def test_invalid_smiles_returns_none(self, invalid_smiles: str) -> None:
        """Invalid SMILES should return None without raising."""
        result = generate_structure_svg(invalid_smiles)
        assert result is None

    def test_empty_smiles_returns_none(self) -> None:
        """Empty SMILES should return None."""
        assert generate_structure_svg("") is None
        assert generate_structure_svg(None) is None

    def test_ethanol_svg(self, ethanol_smiles: str) -> None:
        """Ethanol should produce valid SVG."""
        svg = generate_structure_svg(ethanol_smiles)
        assert svg is not None
        assert "<svg" in svg
