from __future__ import annotations

import os
import re
from datetime import datetime
from typing import Optional


_MOL_ID_RE = re.compile(r"\b([A-Z]{1,4}-[A-Z0-9]{1,4}-\d{3,6})\b", re.IGNORECASE)
_FILENAME_DATE_RE = re.compile(r"(?<!\d)(20\d{2})(\d{2})(\d{2})(?=_)", re.IGNORECASE)


def normalize_molecule_id(value: str | None) -> str:
    raw = (value or "").strip().upper()
    if not raw:
        return ""

    match = _MOL_ID_RE.search(raw)
    if match:
        return match.group(1).upper()

    return re.sub(r"\s+", " ", raw)


def extract_report_date_from_filename(filename: str | None) -> Optional[str]:
    base = os.path.basename(filename or "")
    match = _FILENAME_DATE_RE.search(base)
    if not match:
        return None

    try:
        return datetime.strptime("".join(match.groups()), "%Y%m%d").date().isoformat()
    except ValueError:
        return None
