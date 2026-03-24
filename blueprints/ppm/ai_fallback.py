"""
PPM AI Fallback Module
======================
Uses the Portkey client routed to the internal Galileo endpoint (Gemini Vision).
Only invoked when the rule-based extractor cannot confidently process a page
(e.g., low-quality scan, missing text layer, ambiguous color detection).
"""

import base64
import json
import os
from dotenv import load_dotenv

load_dotenv()


def get_portkey_client():
    """Lazily initialise the Portkey client to avoid import errors when
    the package is not installed and the fallback is never used."""
    try:
        from portkey_ai import Portkey  # type: ignore
    except ImportError as exc:
        raise RuntimeError(
            "portkey-ai is not installed. Run: pip install portkey-ai"
        ) from exc

    api_key = os.getenv("PORTKEY_API_KEY")
    base_url = os.getenv("GALILEO_RCN_ENDPOINT")

    if not api_key:
        raise RuntimeError("PORTKEY_API_KEY is not set in the environment.")
    if not base_url:
        raise RuntimeError("GALILEO_RCN_ENDPOINT is not set in the environment.")

    portkey = Portkey(
        api_key=api_key,
        base_url=base_url,
        debug=False,
    )
    return portkey


_SYSTEM_PROMPT = """You are a precise document parser for pharmaceutical project status reports.
You will be shown an image of a single "Proposal" page from a weekly PDF report.
Your task is to identify ALL molecule status boxes on the page and return ONLY a JSON array.

Each element in the array must have EXACTLY these two keys:
- "molecule_id": string — the molecule identifier found directly below or inside the colored box
  (format examples: DN-AB-065, DN-CD-123, DN-T1-001)
- "status": string — one of these EXACT values based on the border color of the box:
    "In plan"            (cyan / light blue border)
    "In progress"        (yellow border)
    "Obtained"           (green border)
    "Delivered"          (dark blue border)
    "On hold"            (purple / violet border)
    "Cancelled/Stopped"  (red border)

Return ONLY the JSON array, no markdown fences, no extra text.
If no molecule boxes are found on this page, return an empty array: []
"""


def analyse_page_with_ai(page_image_bytes: bytes, project_id: str, theme_id: str,
                          week_date: str, page_number: int) -> list[dict]:
    """
    Send a rendered page image to Gemini Vision via Portkey and return a list of
    molecule status records enriched with project/theme context.

    Args:
        page_image_bytes: PNG bytes of the rendered page.
        project_id: Active project ID from state tracking.
        theme_id: Active theme ID from state tracking.
        week_date: ISO date string for this report (e.g. "2026-03-17").
        page_number: 1-based page number for logging.

    Returns:
        List of dicts with keys: project_id, theme_id, molecule_id, status, week_date, page_number.
        Returns empty list on any failure to let the caller flag the job for review.
    """
    portkey = get_portkey_client()

    b64_image = base64.b64encode(page_image_bytes).decode("utf-8")

    try:
        response = portkey.chat.completions.create(
            model="gemini-2.5-pro",  # Gemini Vision via Galileo routing
            messages=[
                {
                    "role": "system",
                    "content": _SYSTEM_PROMPT,
                },
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": f"data:image/png;base64,{b64_image}",
                            },
                        },
                        {
                            "type": "text",
                            "text": (
                                f"This page belongs to Project {project_id}, Theme {theme_id}. "
                                "Please identify all molecule status boxes and return the JSON array."
                            ),
                        },
                    ],
                },
            ],
            temperature=0,
            max_tokens=2048,
        )

        raw_text = response.choices[0].message.content.strip()

        # Strip accidental markdown fences
        if raw_text.startswith("```"):
            raw_text = "\n".join(
                line for line in raw_text.splitlines()
                if not line.strip().startswith("```")
            ).strip()

        ai_records = json.loads(raw_text)

        if not isinstance(ai_records, list):
            return []

    except Exception:  # noqa: BLE001 — any failure triggers manual review flag
        return []

    valid_statuses = {
        "In plan", "In progress", "Obtained", "Delivered", "On hold", "Cancelled/Stopped"
    }

    enriched = []
    for rec in ai_records:
        mol_id = (rec.get("molecule_id") or "").strip()
        status = (rec.get("status") or "").strip()
        if mol_id and status in valid_statuses:
            enriched.append({
                "project_id": project_id,
                "theme_id": theme_id,
                "molecule_id": mol_id,
                "status": status,
                "week_date": week_date,
                "page_number": page_number,
            })

    return enriched
