"""
calls/utils.py

Shared utilities for ElevenLabs call data processing.

Single source of truth for:
  - ElevenLabs status → Call.Status mapping
  - Transcript formatting
  - Applying post-call results (Call update + Application status transition)

Imported by webhooks/views.py, scheduler/jobs.py, and calls/services.py.
"""

import logging

from django.db import transaction
from django.utils import timezone

from applications.models import Application
from calls.models import Call

logger = logging.getLogger(__name__)

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


# Terminal Call.Status values that indicate a call has ended.
_TERMINAL_STATUSES = frozenset({
    Call.Status.COMPLETED,
    Call.Status.FAILED,
    Call.Status.NO_ANSWER,
    Call.Status.BUSY,
})


def apply_call_result(call: Call, data: dict) -> tuple[str, bool]:
    """
    Apply an ElevenLabs post-call result to a Call record and advance the
    related Application's status.

    This is the **single source of truth** for the "receive call outcome →
    persist → advance pipeline" sequence. Both the webhook view and the
    sync_stuck_calls polling job delegate here.

    Args:
        call: A Call instance (with application, candidate, position
              prefetched via select_related).
        data: The ElevenLabs data dict — either from the webhook payload's
              ``data`` key, or from a direct API poll response.

    Returns:
        A ``(call_status, is_completed)`` tuple so the caller can decide
        whether to trigger downstream processing (e.g. Claude evaluation).
    """
    raw_status = (data.get("status") or "").lower()
    call_status = map_elevenlabs_status(raw_status)
    is_completed = call_status == Call.Status.COMPLETED

    transcript_turns = data.get("transcript") or []
    formatted_transcript = format_transcript(transcript_turns)
    analysis = data.get("analysis") or {}
    metadata = data.get("metadata") or {}

    call.status = call_status

    if formatted_transcript:
        call.transcript = formatted_transcript
    if analysis.get("transcript_summary"):
        call.summary = analysis["transcript_summary"]
    if analysis.get("call_summary_title"):
        call.summary_title = analysis["call_summary_title"]

    recording_url = data.get("recording_url")
    if recording_url:
        call.recording_url = recording_url

    duration = metadata.get("call_duration_secs") or data.get("duration_seconds")
    if duration is not None:
        call.duration_seconds = int(duration)

    if call_status in _TERMINAL_STATUSES:
        call.ended_at = timezone.now()

    with transaction.atomic():
        call.save()

        application = call.application

        if is_completed:
            application.status = Application.Status.CALL_COMPLETED
            application.save(update_fields=["status", "updated_at"])
            application.status = Application.Status.SCORING
            application.save(update_fields=["status", "updated_at"])

        elif call_status in (Call.Status.FAILED, Call.Status.NO_ANSWER, Call.Status.BUSY):
            application.status = Application.Status.CALL_FAILED
            application.save(update_fields=["status", "updated_at"])

    return call_status, is_completed
