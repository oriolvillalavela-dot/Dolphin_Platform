"""Synthesis Agent — LLM-driven retrosynthetic reasoning with RDKit validation.

This module demonstrates a clean, typed, modular architecture for integrating
large language models with cheminformatics. The ``SynthesisAgent`` class accepts
a target molecule (as SMILES), computes RDKit descriptors, and queries an LLM
gateway for retrosynthetic route proposals.

Example::

    >>> agent = SynthesisAgent(api_key="pk-...")
    >>> desc = MoleculeDescriptor.from_smiles("CC(=O)Oc1ccccc1C(=O)O")
    >>> print(desc.molecular_weight)
    180.16

References:
    - Schwaller et al. (2019). "Molecular Transformer" — DOI:10.1021/acscentsci.9b00576
    - Boiko et al. (2023). "ChemCrow" — arXiv:2304.05376
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

from rdkit import Chem
from rdkit.Chem import Descriptors, rdMolDraw2D

logger = logging.getLogger(__name__)


# ────────────────────────────────────────────────────────────────────
# Data Models
# ────────────────────────────────────────────────────────────────────


class ReactionStrategy(Enum):
    """Available retrosynthetic search strategies."""

    SINGLE_STEP = "single_step"
    MULTI_STEP = "multi_step"
    TEMPLATE_BASED = "template_based"


@dataclass(frozen=True)
class MoleculeDescriptor:
    """Immutable descriptor for a chemical entity, computed from SMILES.

    Attributes:
        smiles: Canonical SMILES string.
        molecular_weight: Molecular weight in Daltons.
        logp: Wildman-Crippen LogP.
        hbd: Number of hydrogen-bond donors.
        hba: Number of hydrogen-bond acceptors.
        rotatable_bonds: Number of rotatable bonds.
        tpsa: Topological polar surface area (Å²).
        functional_groups: Detected functional group names.
        name: Optional human-readable name.
    """

    smiles: str
    molecular_weight: float
    logp: float
    hbd: int
    hba: int
    rotatable_bonds: int
    tpsa: float
    functional_groups: list[str] = field(default_factory=list)
    name: Optional[str] = None

    @classmethod
    def from_smiles(cls, smiles: str, name: Optional[str] = None) -> MoleculeDescriptor:
        """Construct a ``MoleculeDescriptor`` from a SMILES string.

        Args:
            smiles: A valid SMILES string representing the molecule.
            name: Optional human-readable molecule name.

        Returns:
            A ``MoleculeDescriptor`` with all computed properties.

        Raises:
            ValueError: If the SMILES string cannot be parsed by RDKit.
        """
        mol = Chem.MolFromSmiles(smiles)
        if mol is None:
            raise ValueError(f"Invalid SMILES: '{smiles}' could not be parsed by RDKit.")

        canonical = Chem.MolToSmiles(mol, canonical=True)

        return cls(
            smiles=canonical,
            molecular_weight=round(Descriptors.MolWt(mol), 2),
            logp=round(Descriptors.MolLogP(mol), 2),
            hbd=Descriptors.NumHDonors(mol),
            hba=Descriptors.NumHAcceptors(mol),
            rotatable_bonds=Descriptors.NumRotatableBonds(mol),
            tpsa=round(Descriptors.TPSA(mol), 2),
            functional_groups=_detect_functional_groups(mol),
            name=name,
        )

    def to_svg(self, width: int = 350, height: int = 300) -> str:
        """Render the molecule as an SVG string.

        Args:
            width: Image width in pixels.
            height: Image height in pixels.

        Returns:
            SVG markup string of the 2D molecular structure.
        """
        mol = Chem.MolFromSmiles(self.smiles)
        drawer = rdMolDraw2D.MolDraw2DSVG(width, height)
        drawer.DrawMolecule(mol)
        drawer.FinishDrawing()
        return drawer.GetDrawingText()

    @property
    def passes_lipinski(self) -> bool:
        """Check Lipinski's Rule of Five (drug-likeness filter).

        Returns:
            ``True`` if the molecule satisfies all four Lipinski criteria.
        """
        return (
            self.molecular_weight <= 500
            and self.logp <= 5
            and self.hbd <= 5
            and self.hba <= 10
        )


@dataclass(frozen=True)
class SyntheticRoute:
    """A single proposed retrosynthetic disconnection.

    Attributes:
        precursors: List of precursor SMILES strings.
        target: Target product SMILES.
        confidence: Model confidence score (0.0–1.0).
        reaction_type: Predicted reaction classification.
        reasoning: Natural-language explanation of the disconnection.
    """

    precursors: list[str]
    target: str
    confidence: float
    reaction_type: Optional[str] = None
    reasoning: Optional[str] = None


@dataclass
class RetrosynthesisResult:
    """Container for retrosynthetic analysis output.

    Attributes:
        target_smiles: The query molecule SMILES.
        strategy: The search strategy used.
        routes: Ranked list of proposed synthetic routes.
        model_id: Identifier of the LLM model used.
    """

    target_smiles: str
    strategy: ReactionStrategy
    routes: list[SyntheticRoute]
    model_id: str = "portkey-default"


# ────────────────────────────────────────────────────────────────────
# Core Agent
# ────────────────────────────────────────────────────────────────────


class SynthesisAgent:
    """LLM-powered retrosynthetic reasoning agent.

    Combines RDKit molecular descriptors with structured LLM prompting
    to propose plausible retrosynthetic routes for a target molecule.

    Args:
        api_key: API key for the Portkey AI gateway.
        model: LLM model identifier (default: ``"gpt-4o"``).
        temperature: Sampling temperature for the LLM (default: 0.2).

    Example::

        >>> agent = SynthesisAgent(api_key="pk-test-key")
        >>> result = agent.propose_retrosynthesis("c1ccccc1")
        >>> print(len(result.routes))
    """

    def __init__(
        self,
        api_key: str,
        model: str = "gpt-4o",
        temperature: float = 0.2,
    ) -> None:
        self._api_key = api_key
        self._model = model
        self._temperature = temperature

    def propose_retrosynthesis(
        self,
        target_smiles: str,
        strategy: str | ReactionStrategy = ReactionStrategy.SINGLE_STEP,
        max_proposals: int = 3,
    ) -> RetrosynthesisResult:
        """Propose retrosynthetic routes for a target molecule.

        Constructs a structured prompt enriched with RDKit-computed molecular
        descriptors and submits it to the LLM for reasoning.

        Args:
            target_smiles: SMILES string of the target molecule.
            strategy: Retrosynthetic search strategy to employ.
            max_proposals: Maximum number of routes to return (1–10).

        Returns:
            A ``RetrosynthesisResult`` containing ranked route proposals.

        Raises:
            ValueError: If the target SMILES is invalid.
        """
        if isinstance(strategy, str):
            strategy = ReactionStrategy(strategy)

        # Validate and compute descriptors
        descriptor = MoleculeDescriptor.from_smiles(target_smiles)

        prompt = self._build_prompt(descriptor, strategy, max_proposals)
        logger.info(
            "Requesting retrosynthesis for %s (MW=%.1f, strategy=%s)",
            descriptor.smiles,
            descriptor.molecular_weight,
            strategy.value,
        )

        # In production, this calls the Portkey AI gateway.
        # Here we return a structured placeholder to demonstrate the interface.
        routes = self._call_llm(prompt, max_proposals)

        return RetrosynthesisResult(
            target_smiles=descriptor.smiles,
            strategy=strategy,
            routes=routes,
            model_id=self._model,
        )

    def _build_prompt(
        self,
        descriptor: MoleculeDescriptor,
        strategy: ReactionStrategy,
        max_proposals: int,
    ) -> str:
        """Build a structured retrosynthesis prompt for the LLM.

        Args:
            descriptor: Pre-computed molecular descriptors.
            strategy: The search strategy.
            max_proposals: Number of routes to request.

        Returns:
            A formatted prompt string.
        """
        context = {
            "smiles": descriptor.smiles,
            "molecular_weight": descriptor.molecular_weight,
            "logp": descriptor.logp,
            "hbd": descriptor.hbd,
            "hba": descriptor.hba,
            "functional_groups": descriptor.functional_groups,
            "lipinski_pass": descriptor.passes_lipinski,
        }

        return (
            f"You are an expert synthetic chemist. Propose up to {max_proposals} "
            f"retrosynthetic routes for the following target molecule.\n\n"
            f"**Target SMILES**: {descriptor.smiles}\n"
            f"**Molecular properties**: {json.dumps(context, indent=2)}\n"
            f"**Strategy**: {strategy.value}\n\n"
            f"For each route, provide:\n"
            f"1. Precursor SMILES (dot-separated)\n"
            f"2. Reaction type (e.g., Suzuki coupling, amide bond formation)\n"
            f"3. Confidence score (0.0–1.0)\n"
            f"4. Brief reasoning\n\n"
            f"Return valid JSON array of objects with keys: "
            f"precursors, reaction_type, confidence, reasoning."
        )

    def _call_llm(self, prompt: str, max_proposals: int) -> list[SyntheticRoute]:
        """Send prompt to LLM and parse the response.

        Note:
            This is a demonstration stub. In production, replace with
            actual Portkey AI gateway calls using ``httpx.AsyncClient``.

        Args:
            prompt: The constructed prompt string.
            max_proposals: Expected number of proposals.

        Returns:
            List of parsed ``SyntheticRoute`` objects.
        """
        logger.debug("LLM prompt length: %d characters", len(prompt))

        # Placeholder: return empty routes
        # Production implementation would use:
        #   from portkey_ai import Portkey
        #   client = Portkey(api_key=self._api_key)
        #   response = client.chat.completions.create(...)
        return []


# ────────────────────────────────────────────────────────────────────
# Helpers
# ────────────────────────────────────────────────────────────────────

# Core SMARTS patterns for functional group detection (subset)
_FUNCTIONAL_GROUP_SMARTS: dict[str, str] = {
    "alcohol": "[OX2H][CX4]",
    "aldehyde": "[CX3H1](=O)[#6]",
    "ketone": "[#6][CX3](=O)[#6]",
    "carboxylic_acid": "[CX3](=O)[OX2H1]",
    "ester": "[CX3](=O)[OX2H0][#6]",
    "amine_primary": "[NX3;H2][#6]",
    "amide": "[CX3](=O)[NX3]",
    "nitrile": "[CX2]#N",
    "aromatic_ring": "a1aaaaa1",
    "boronic_acid": "[BX3]([OX2H])[OX2H]",
}


def _detect_functional_groups(mol: Chem.Mol) -> list[str]:
    """Detect functional groups in a molecule using SMARTS matching.

    Args:
        mol: An RDKit ``Mol`` object.

    Returns:
        Sorted list of detected functional group names.
    """
    detected: list[str] = []
    for name, smarts in _FUNCTIONAL_GROUP_SMARTS.items():
        pattern = Chem.MolFromSmarts(smarts)
        if pattern is not None and mol.HasSubstructMatch(pattern):
            detected.append(name)
    return sorted(detected)
