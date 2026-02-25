"""
calls/utils.py

Shared utilities for ElevenLabs call data processing.

Imported by both webhooks/views.py and scheduler/jobs.py to eliminate
duplicate implementations of transcript formatting and status mapping.
"""

from calls.models import Call

# ElevenLabs status string → internal Call.Status.
# Single source of truth: extend here when ElevenLabs adds new status values.
_EL_STATUS_MAP: dict[str, str] = {
    "done": Call.Status.COMPLETED,
    "completed": Call.Status.COMPLETED,
    "failed": Call.Status.FAILED,
    "no_answer": Call.Status.NO_ANSWER,
    "busy": Call.Status.BUSY,
    "in_progress": Call.Status.IN_PROGRESS,
    "processing": Call.Status.IN_PROGRESS,
}


def map_elevenlabs_status(raw_status: str) -> str:
    """
    Map an ElevenLabs call status string to the internal Call.Status value.
    Defaults to IN_PROGRESS for any unrecognised value.
    Spec § 9 — Status mapping.
    """
    return _EL_STATUS_MAP.get((raw_status or "").lower(), Call.Status.IN_PROGRESS)


def format_transcript(turns: list) -> str:
    """
    Convert ElevenLabs transcript turns into a human-readable dialogue string.

    ElevenLabs turn objects may use 'message', 'content', or 'text' as the
    field name for the spoken text — we check all three.

    Spec § 9 — Transcript Format:
      Agent: Hello, this is a call regarding...

      User: Yes, hello...
    """
    if not turns:
        return ""

    lines = []
    for turn in turns:
        role = (turn.get("role") or "").capitalize()
        text = (
            turn.get("message")
            or turn.get("content")
            or turn.get("text")
            or ""
        ).strip()
        if role and text:
            lines.append(f"{role}: {text}")

    return "\n\n".join(lines)
