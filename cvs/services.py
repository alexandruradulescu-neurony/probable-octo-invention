"""
cvs/services.py

CV-related services.

extract_cv_data_via_haiku — uses Claude Haiku to extract structured contact
data (name, email, phone) from raw CV text, for candidate matching.
"""

import json
import logging
import re

import anthropic
from django.conf import settings

logger = logging.getLogger(__name__)


# ── Custom exception ───────────────────────────────────────────────────────────

class CVExtractionError(Exception):
    """Raised when CV data extraction via Claude fails."""


# ── JSON fence stripper (same pattern as evaluations/services.py) ──────────────

_JSON_FENCE_RE = re.compile(r"```(?:json)?\s*([\s\S]*?)```", re.IGNORECASE)


# ── Public function ────────────────────────────────────────────────────────────

def extract_cv_data_via_haiku(text_content: str) -> dict:
    """
    Extract candidate contact details from raw CV text using Claude Haiku.

    Uses the fast/cheap Haiku model (ANTHROPIC_FAST_MODEL) since this is a
    simple structured extraction task — no reasoning required.

    Args:
        text_content: Plain text extracted from a CV document (e.g. via pdfplumber).

    Returns:
        dict with keys:
          first_name (str | None)
          last_name  (str | None)
          email      (str | None)
          phone      (str | None)

        All values are None when not found in the CV text.

    Raises:
        CVExtractionError on API failure or unparseable response.
    """
    api_key = settings.ANTHROPIC_API_KEY
    if not api_key:
        raise CVExtractionError("ANTHROPIC_API_KEY is not configured.")

    model = settings.ANTHROPIC_FAST_MODEL
    if not model:
        raise CVExtractionError("ANTHROPIC_FAST_MODEL is not configured.")

    logger.debug("Extracting CV data via Haiku (%s)", model)

    system_prompt = (
        "You are a precise data extraction assistant. "
        "Extract contact information from CV/resume text. "
        "Respond ONLY with a valid JSON object — no prose, no markdown fences."
    )

    user_message = (
        "Extract the following fields from the CV text below. "
        "If a field cannot be found, use null.\n\n"
        "Return exactly this JSON schema:\n"
        "{\n"
        '  "first_name": "<first name or null>",\n'
        '  "last_name": "<last name or null>",\n'
        '  "email": "<email address or null>",\n'
        '  "phone": "<phone number or null>"\n'
        "}\n\n"
        f"--- CV TEXT START ---\n{text_content}\n--- CV TEXT END ---"
    )

    try:
        client = anthropic.Anthropic(api_key=api_key)
        message = client.messages.create(
            model=model,
            max_tokens=256,
            system=system_prompt,
            messages=[{"role": "user", "content": user_message}],
        )
    except anthropic.APIError as exc:
        raise CVExtractionError(f"Anthropic API error during CV extraction: {exc}") from exc

    if not message.content:
        raise CVExtractionError("Anthropic returned an empty response for CV extraction.")

    raw = message.content[0].text.strip()

    # Strip optional markdown fences
    fence_match = _JSON_FENCE_RE.search(raw)
    if fence_match:
        raw = fence_match.group(1).strip()

    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise CVExtractionError(
            f"Failed to parse CV extraction JSON: {exc}. Raw: {raw[:200]}"
        ) from exc

    if not isinstance(data, dict):
        raise CVExtractionError(
            f"Expected JSON object from Haiku, got {type(data).__name__}."
        )

    # Normalise: ensure all four keys exist, coerce empty strings to None
    result = {}
    for field in ("first_name", "last_name", "email", "phone"):
        value = data.get(field)
        result[field] = str(value).strip() if value else None

    logger.debug("CV extraction result: %s", result)
    return result
