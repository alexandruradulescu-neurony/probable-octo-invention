import re

_JSON_FENCE_RE = re.compile(r"```(?:json)?\s*([\s\S]*?)```", re.IGNORECASE)


def strip_json_fence(raw: str) -> str:
    """Return raw text with optional ```json fences removed."""
    text = (raw or "").strip()
    match = _JSON_FENCE_RE.search(text)
    if match:
        return match.group(1).strip()
    return text


def build_full_name(first_name: str, last_name: str) -> str:
    """Build a normalized full name from first/last name components."""
    return f"{first_name or ''} {last_name or ''}".strip()


def humanize_form_question(key: str) -> str:
    """Transform snake_case form key into a readable question label."""
    return (key or "").replace("_", " ").strip().capitalize()
