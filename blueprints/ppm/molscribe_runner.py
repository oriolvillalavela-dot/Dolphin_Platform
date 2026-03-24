"""
MolScribe Lazy-Loaded Singleton Runner
=======================================
Provides a single module-level function `predict_smiles(png_bytes: bytes) -> str | None`
that runs MolScribe molecular structure recognition on a PNG image.

Design principles:
  - Lazy loading: model is loaded on first call, NOT at import time or server startup.
  - Singleton: model is loaded once and kept in memory.
  - Retry-once guard: if loading fails, we don't retry on every PDF page (expensive).
  - Graceful degradation: any failure returns None; the pipeline continues without SMILES.
  - Checkpoint resolution priority:
      1. <ppm_dir>/checkpoints/swin_base_char_aux_1m680k.pth   (project-local override)
      2. ~/.cache/molscribe/swin_base_char_aux_1m680k.pth       (persistent user cache)
      3. Auto-download from HuggingFace Hub (yujieq/MolScribe)  (first-run download)
"""

from __future__ import annotations

import io
import os
import logging
from typing import Optional

logger = logging.getLogger(__name__)

# ── Checkpoint location constants ─────────────────────────────────────────────
_CHECKPOINT_FILENAME = "swin_base_char_aux_1m680k.pth"
_HUGGINGFACE_REPO    = "yujieq/MolScribe"
_CACHE_DIR           = os.path.expanduser("~/.cache/molscribe")
_LOCAL_CKPT_DIR      = os.path.join(os.path.dirname(__file__), "checkpoints")

# ── Singleton state ───────────────────────────────────────────────────────────
_model              = None   # MolScribe instance once loaded
_load_attempted     = False  # True after first load attempt (success or failure)
_model_unavailable  = False  # True if MolScribe not installed


def _find_or_download_checkpoint() -> Optional[str]:
    """
    Resolve the MolScribe checkpoint file.
    Returns the absolute path to the .pth file, or None if unavailable.
    """
    # 1. Project-local override (useful for air-gapped environments)
    local = os.path.join(_LOCAL_CKPT_DIR, _CHECKPOINT_FILENAME)
    if os.path.exists(local):
        logger.info(f"MolScribe: using local checkpoint at {local}")
        return local

    # 2. User cache directory
    cached = os.path.join(_CACHE_DIR, _CHECKPOINT_FILENAME)
    if os.path.exists(cached):
        logger.info(f"MolScribe: using cached checkpoint at {cached}")
        return cached

    # 3. Download via HuggingFace Hub (first-run only, ~500 MB)
    try:
        from huggingface_hub import hf_hub_download
        logger.info(
            f"MolScribe: downloading checkpoint from HuggingFace Hub "
            f"({_HUGGINGFACE_REPO}/{_CHECKPOINT_FILENAME}) …"
        )
        path = hf_hub_download(
            repo_id=_HUGGINGFACE_REPO,
            filename=_CHECKPOINT_FILENAME,
            cache_dir=_CACHE_DIR,
        )
        logger.info(f"MolScribe: checkpoint downloaded to {path}")
        return path
    except Exception as exc:
        logger.warning(
            f"MolScribe: could not download checkpoint — {exc}. "
            f"Place {_CHECKPOINT_FILENAME} in {_LOCAL_CKPT_DIR} to use manually."
        )
        return None


def _load_model():
    """Load and return a MolScribe model instance, or None on failure."""
    global _model, _load_attempted, _model_unavailable

    if _load_attempted:
        return _model

    _load_attempted = True

    # Check MolScribe is installed
    try:
        import torch
        from molscribe import MolScribe
    except ImportError:
        logger.info(
            "MolScribe not installed — auto-SMILES extraction is disabled. "
            "Install with: pip install MolScribe"
        )
        _model_unavailable = True
        return None

    ckpt = _find_or_download_checkpoint()
    if not ckpt:
        _model_unavailable = True
        return None

    try:
        import torch  # already imported above, but explicit here
        device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
        logger.info(f"MolScribe: loading model on {device} from {ckpt} …")
        _model = MolScribe(ckpt, device=device)
        logger.info("MolScribe: model loaded and ready.")
    except Exception as exc:
        logger.warning(f"MolScribe: model load failed — {exc}")
        _model = None

    return _model


def predict_smiles(png_bytes: bytes) -> Optional[str]:
    """
    Run MolScribe inference on a molecular structure image.

    Args:
        png_bytes: Raw PNG bytes of the cropped molecular structure.

    Returns:
        Canonical SMILES string if prediction succeeds, or None.
    """
    model = _load_model()
    if model is None:
        return None

    try:
        from PIL import Image
        img    = Image.open(io.BytesIO(png_bytes)).convert("RGB")
        output = model.predict_image(img)

        # MolScribe returns a dict with 'smiles' key
        smiles = output.get("smiles") if isinstance(output, dict) else str(output)

        # Filter out known invalid tokens
        _INVALID = {"", "[INVALID]", "Invalid", "invalid", "N/A"}
        if smiles and smiles.strip() not in _INVALID:
            return smiles.strip()
        return None

    except Exception as exc:
        logger.debug(f"MolScribe inference failed: {exc}")
        return None


def is_available() -> bool:
    """
    Return True if MolScribe is installed AND the checkpoint is accessible.
    Triggers the one-time load attempt if not already done.
    """
    return _load_model() is not None
