from __future__ import annotations

import json
import os
from dotenv import load_dotenv

load_dotenv()


class PortkeyLayoutError(RuntimeError):
    pass


def get_portkey_client():
    try:
        from portkey_ai import Portkey  # type: ignore
    except ImportError as exc:
        raise PortkeyLayoutError(
            "portkey-ai is not installed. Run: pip install portkey-ai"
        ) from exc

    api_key = os.getenv("PORTKEY_API_KEY")
    base_url = os.getenv("GALILEO_RCN_ENDPOINT")
    if not api_key:
        raise PortkeyLayoutError("PORTKEY_API_KEY is not set in the environment.")
    if not base_url:
        raise PortkeyLayoutError("GALILEO_RCN_ENDPOINT is not set in the environment.")

    return Portkey(
        api_key=api_key,
        base_url=base_url,
        debug=False,
    )


_SYSTEM_PROMPT = """Role: You are an expert computational chemist and laboratory automation engineer specializing in high-throughput experimentation (HTE) and combinatorial plate design.

Objective: Take a list of chemical components and a target plate size, and generate an optimally mapped 2D combinatorial screening layout. You must return your layout strictly as a JSON object matching the requested schema.

Instructions:

Analyze Inputs: You will receive a target plate size (e.g., 24-well [4 rows x 6 columns], 48-well [6 rows x 8 columns], or 96-well [8 rows x 12 columns]) and a list of components categorized by their roles (Catalyst, Ligand, Reagent, Solvent, Additive).

Identify Constants vs. Variables:
- If a specific role category contains only one chemical option, classify it as a "Global Component" (present in all wells).
- If a specific role category contains multiple chemical options, classify it as a "Screening Variable".

Optimize Axis Mapping (Combinatorial Logic):
- Calculate the optimal way to distribute the Screening Variables across the grid's rows and columns to maximize coverage.
- For example, in a 24-well plate (4x6): If you have 4 Solvents and 6 Ligands, map the 4 Solvents to Rows A-D, and the 6 Ligands to Columns 1-6.
- Combined Variables: If necessary, group variables to fit the axes. For example, if you have 2 Solvents and 2 Reagents for a 4-row plate, create 4 distinct row conditions (Solvent 1 + Reagent 1; Solvent 1 + Reagent 2; Solvent 2 + Reagent 1; Solvent 2 + Reagent 2).

JSON Schema Adherence:
- Your output must be strictly valid JSON.
- Do not include markdown formatting or conversational text outside the JSON object.

Expected JSON Output Structure:
{
  "plate_design_name": "AI_Optimized_Design",
  "dimensions": {
    "rows": "<integer, e.g., 4>",
    "columns": "<integer, e.g., 6>"
  },
  "global_components": [
    { "name": "<string>", "chem_id": "<string>", "role": "<string>", "equivalents": "<string>" }
  ],
  "axes": {
    "rows": [
      {
        "label": "<string, e.g., 'A'>",
        "variables": [
          { "name": "<string>", "chem_id": "<string>", "role": "<string>", "equivalents_or_fraction": "<string>" }
        ]
      }
    ],
    "columns": [
      {
        "label": "<string, e.g., '1'>",
        "variables": [
          { "name": "<string>", "chem_id": "<string>", "role": "<string>", "equivalents_or_fraction": "<string>" }
        ]
      }
    ]
  },
  "wells": {
    "A1": {
      "row_label": "A",
      "column_label": "1",
      "unique_components": []
    }
  }
}
"""


def _extract_content_text(content):
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for item in content:
            if isinstance(item, dict):
                text = item.get("text")
                if text:
                    parts.append(str(text))
            elif isinstance(item, str):
                parts.append(item)
        return "\n".join(parts)
    return str(content or "")


def _strip_fences(text: str) -> str:
    cleaned = text.strip()
    if not cleaned.startswith("```"):
        return cleaned
    return "\n".join(
        line for line in cleaned.splitlines()
        if not line.strip().startswith("```")
    ).strip()


def _extract_first_json_object(text: str) -> str:
    cleaned = _strip_fences(text)
    start = cleaned.find("{")
    if start < 0:
        return cleaned
    depth = 0
    for idx in range(start, len(cleaned)):
        ch = cleaned[idx]
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return cleaned[start:idx + 1]
    return cleaned[start:]


def generate_layout_with_portkey(
    *,
    plate_size: int,
    dimensions: dict,
    components_by_role: dict,
    design_name: str | None = None,
    max_retries: int = 1,
):
    portkey = get_portkey_client()
    model = os.getenv("SCREENINGS_LAYOUT_MODEL", "gemini-2.5-pro")

    user_payload = {
        "target_plate_size": int(plate_size),
        "target_dimensions": dimensions,
        "requested_design_name": design_name or "AI_Optimized_Design",
        "components_by_role": components_by_role,
    }
    user_text = (
        "Generate an optimized combinatorial plate layout.\n"
        "Return only strict JSON that follows the schema in the system prompt.\n"
        f"Input payload:\n{json.dumps(user_payload, ensure_ascii=True)}"
    )

    last_err = None
    for _ in range(max_retries + 1):
        try:
            common_kwargs = dict(
                model=model,
                messages=[
                    {"role": "system", "content": _SYSTEM_PROMPT},
                    {"role": "user", "content": user_text},
                ],
                temperature=0,
                max_tokens=4096,
            )
            try:
                response = portkey.chat.completions.create(
                    **common_kwargs,
                    response_format={"type": "json_object"},
                )
            except Exception:
                response = portkey.chat.completions.create(**common_kwargs)
            raw = _extract_content_text(response.choices[0].message.content)
            parsed = json.loads(_extract_first_json_object(raw))
            if not isinstance(parsed, dict):
                raise ValueError("LLM returned non-object JSON.")
            return parsed
        except Exception as exc:  # noqa: BLE001
            last_err = exc
            continue

    raise PortkeyLayoutError(f"Portkey layout generation failed: {last_err}")
