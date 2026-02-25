"""
scheduler/jobs.py

All background job definitions for the RecruitFlow pipeline.
Registered and started by: scheduler/management/commands/run_scheduler.py

Spec reference: Section 6 — Scheduled Jobs
  process_call_queue   every  5 min
  sync_stuck_calls     every 10 min
  check_cv_followups   every 60 min
  close_stale_rejected every 24 hrs

Each function is decorated with @close_old_connections from django-apscheduler so
that Django DB connections opened in APScheduler's worker threads are always
returned to the pool (or closed) after each run, preventing "connection already
closed" errors in long-running processes.
"""

import logging
from datetime import timedelta
from zoneinfo import ZoneInfo

import requests
from django.conf import settings
from django.db import transaction
from django.utils import timezone
from django_apscheduler.util import close_old_connections

from applications.models import Application
from calls.models import Call
from calls.services import ElevenLabsError, ElevenLabsService
from evaluations.services import ClaudeService, ClaudeServiceError
from messaging.models import Message
from messaging.services import send_followup

logger = logging.getLogger(__name__)

# ── Constants ──────────────────────────────────────────────────────────────────

# A call is considered "stuck" if it has been in initiated/in_progress longer
# than this threshold without the webhook delivering a completion event.
STUCK_CALL_THRESHOLD_MINUTES = 15

ELEVENLABS_BASE_URL = "https://api.elevenlabs.io"

# Endpoints tried in spec-order when polling for a stuck call's state.
# Spec § 9 — Fallback Polling
_POLL_ENDPOINT_TEMPLATES = [
    "/v1/convai/conversations/{id}",
    "/v1/convai/calls/{id}",
    "/v1/conversations/{id}",
    "/v1/calls/{id}",
]

# ElevenLabs status → internal Call.Status mapping (shared with webhook layer)
_EL_STATUS_MAP = {
    "done": Call.Status.COMPLETED,
    "completed": Call.Status.COMPLETED,
    "failed": Call.Status.FAILED,
    "no_answer": Call.Status.NO_ANSWER,
    "busy": Call.Status.BUSY,
    "in_progress": Call.Status.IN_PROGRESS,
    "processing": Call.Status.IN_PROGRESS,
}


# ─────────────────────────────────────────────────────────────────────────────
# Job 1: process_call_queue  (every 5 min)
# ─────────────────────────────────────────────────────────────────────────────

@close_old_connections
def process_call_queue() -> None:
    """
    Find applications ready to be called and place outbound calls via ElevenLabs.

    Two queues are processed differently:
      1. CALL_QUEUED        — All eligible applications are collected and submitted
                              as a single batch via the ElevenLabs batch-calling API.
                              Conversation IDs are assigned asynchronously by webhook.
      2. CALLBACK_SCHEDULED — One-off calls with specific scheduled times; submitted
                              individually via the single-call API (as before).

    Calling-hours gate: calls are only placed between
    Position.calling_hour_start and Position.calling_hour_end (inclusive start,
    exclusive end), evaluated in the APSCHEDULER_TIMEZONE.

    Spec § 6 — process_call_queue, § 10 step 3.
    """
    now = timezone.now()
    tz = ZoneInfo(settings.APSCHEDULER_TIMEZONE)
    current_hour = now.astimezone(tz).hour

    service = ElevenLabsService()

    # ── Queue 1: batch — collect all eligible queued applications ─────────────
    queued = (
        Application.objects
        .filter(status=Application.Status.CALL_QUEUED)
        .select_related("candidate", "position")
    )

    eligible_for_batch = [
        app for app in queued
        if _is_within_calling_hours(app.position, current_hour)
    ]

    for app in queued:
        if app not in eligible_for_batch:
            logger.debug(
                "Skipping application=%s — outside calling hours (hour=%s, window=%s–%s)",
                app.pk, current_hour,
                app.position.calling_hour_start,
                app.position.calling_hour_end,
            )

    queued_count = 0
    if eligible_for_batch:
        try:
            created_calls = service.initiate_batch_calls(eligible_for_batch)
            queued_count = len(created_calls)
        except ElevenLabsError as exc:
            logger.error(
                "Batch call submission failed: %s — marking %s application(s) as CALL_FAILED",
                exc,
                len(eligible_for_batch),
                exc_info=True,
            )
            with transaction.atomic():
                Application.objects.filter(
                    pk__in=[a.pk for a in eligible_for_batch]
                ).update(status=Application.Status.CALL_FAILED, updated_at=now)

    # ── Queue 2: individual — scheduled callbacks whose time has arrived ───────
    callbacks = (
        Application.objects
        .filter(
            status=Application.Status.CALLBACK_SCHEDULED,
            callback_scheduled_at__lte=now,
        )
        .select_related("candidate", "position")
    )

    callback_count = 0
    for app in callbacks:
        if not _is_within_calling_hours(app.position, current_hour):
            logger.debug(
                "Skipping callback application=%s — outside calling hours", app.pk
            )
            continue
        _attempt_call(service, app)
        callback_count += 1

    if queued_count or callback_count:
        logger.info(
            "process_call_queue: submitted %s queued (batch) + %s callback (individual) calls",
            queued_count,
            callback_count,
        )


def _is_within_calling_hours(position, current_hour: int) -> bool:
    """Return True if current_hour falls within the position's calling window."""
    start = position.calling_hour_start
    end = position.calling_hour_end
    if start >= end:
        # Defensive: misconfigured position — skip all calls
        logger.warning(
            "Position=%s has invalid calling hours (%s >= %s) — skipping",
            position.pk, start, end,
        )
        return False
    return start <= current_hour < end


def _attempt_call(service: ElevenLabsService, app: Application) -> None:
    """
    Try to place an outbound call for an application.
    On ElevenLabsError, mark the application CALL_FAILED and log.
    """
    try:
        call = service.initiate_outbound_call(app)
        logger.info(
            "Call initiated: application=%s call=%s conversation_id=%s",
            app.pk,
            call.pk,
            call.eleven_labs_conversation_id,
        )
    except ElevenLabsError as exc:
        logger.error(
            "Failed to initiate call for application=%s: %s",
            app.pk,
            exc,
            exc_info=True,
        )
        with transaction.atomic():
            app.status = Application.Status.CALL_FAILED
            app.save(update_fields=["status", "updated_at"])


# ─────────────────────────────────────────────────────────────────────────────
# Job 2: sync_stuck_calls  (every 10 min)
# ─────────────────────────────────────────────────────────────────────────────

@close_old_connections
def sync_stuck_calls() -> None:
    """
    Webhook fallback: poll ElevenLabs directly for calls that have been stuck
    in INITIATED or IN_PROGRESS beyond the threshold window.

    For each stuck call:
      - Try each ElevenLabs polling endpoint in spec order.
      - Update Call with transcript / summary / status.
      - If completed → trigger Claude evaluation.
      - If failed / no_answer → mark Application CALL_FAILED.

    Spec § 9 — Fallback Polling, § 6 — sync_stuck_calls.
    """
    threshold_time = timezone.now() - timedelta(minutes=STUCK_CALL_THRESHOLD_MINUTES)

    stuck_calls = (
        Call.objects
        .filter(
            status__in=[Call.Status.INITIATED, Call.Status.IN_PROGRESS],
            initiated_at__lt=threshold_time,
        )
        .exclude(eleven_labs_conversation_id__isnull=True)
        .exclude(eleven_labs_conversation_id="")
        .select_related(
            "application__candidate",
            "application__position",
        )
    )

    if not stuck_calls.exists():
        return

    api_key = settings.ELEVENLABS_API_KEY
    if not api_key:
        logger.warning("sync_stuck_calls: ELEVENLABS_API_KEY not set — skipping poll")
        return

    processed = 0
    for call in stuck_calls:
        data = _poll_elevenlabs_call(call.eleven_labs_conversation_id, api_key)
        if data is None:
            logger.warning(
                "sync_stuck_calls: no response from any ElevenLabs endpoint "
                "for conversation_id=%s (call=%s)",
                call.eleven_labs_conversation_id,
                call.pk,
            )
            continue

        _update_call_from_poll(call, data)
        processed += 1

    logger.info("sync_stuck_calls: processed %s stuck call(s)", processed)


def _poll_elevenlabs_call(conversation_id: str, api_key: str) -> dict | None:
    """
    Attempt to retrieve call data from ElevenLabs by trying each endpoint
    in the spec-mandated order.  Returns the first successful JSON response,
    or None if all attempts fail.

    Spec § 9 — Fallback Polling endpoints:
      GET /v1/convai/conversations/{id}
      GET /v1/convai/calls/{id}
      GET /v1/conversations/{id}
      GET /v1/calls/{id}
    """
    headers = {"xi-api-key": api_key}

    for template in _POLL_ENDPOINT_TEMPLATES:
        url = ELEVENLABS_BASE_URL + template.format(id=conversation_id)
        try:
            resp = requests.get(url, headers=headers, timeout=20)
        except requests.RequestException as exc:
            logger.debug("ElevenLabs poll network error %s: %s", url, exc)
            continue

        if resp.status_code == 404:
            continue  # Try next endpoint
        if resp.ok:
            try:
                return resp.json()
            except ValueError:
                logger.debug("ElevenLabs poll non-JSON from %s", url)
                continue
        else:
            logger.debug(
                "ElevenLabs poll HTTP %s from %s", resp.status_code, url
            )

    return None


def _update_call_from_poll(call: Call, data: dict) -> None:
    """
    Apply the polled ElevenLabs data to the Call record and trigger downstream
    processing (Claude evaluation) if the call has completed.
    """
    raw_status = (data.get("status") or "").lower()
    call_status = _EL_STATUS_MAP.get(raw_status, Call.Status.IN_PROGRESS)
    is_completed = call_status == Call.Status.COMPLETED

    transcript_turns = data.get("transcript") or []
    formatted_transcript = _format_transcript(transcript_turns)
    analysis = data.get("analysis") or {}

    with transaction.atomic():
        call.status = call_status
        if formatted_transcript:
            call.transcript = formatted_transcript
        if analysis.get("transcript_summary"):
            call.summary = analysis["transcript_summary"]
        if analysis.get("call_summary_title"):
            call.summary_title = analysis["call_summary_title"]
        if data.get("recording_url"):
            call.recording_url = data["recording_url"]
        duration = (
            (data.get("metadata") or {}).get("call_duration_secs")
            or data.get("duration_seconds")
        )
        if duration is not None:
            call.duration_seconds = int(duration)
        if is_completed or call_status in (
            Call.Status.FAILED, Call.Status.NO_ANSWER, Call.Status.BUSY
        ):
            call.ended_at = timezone.now()
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

    logger.info(
        "sync_stuck_calls: updated call=%s status=%s application=%s",
        call.pk,
        call_status,
        call.application_id,
    )

    if is_completed:
        try:
            evaluation = ClaudeService().evaluate_call(call)
            logger.info(
                "sync_stuck_calls: evaluation=%s outcome=%s for call=%s",
                evaluation.pk,
                evaluation.outcome,
                call.pk,
            )
        except ClaudeServiceError as exc:
            logger.error(
                "sync_stuck_calls: Claude evaluation failed for call=%s: %s",
                call.pk, exc, exc_info=True,
            )
        except Exception as exc:  # noqa: BLE001
            logger.error(
                "sync_stuck_calls: unexpected error evaluating call=%s: %s",
                call.pk, exc, exc_info=True,
            )


# ─────────────────────────────────────────────────────────────────────────────
# Job 3: check_cv_followups  (every 60 min)
# ─────────────────────────────────────────────────────────────────────────────

# Maps current application status → (next status, message type to send or None)
_FOLLOWUP_TRANSITIONS = {
    Application.Status.AWAITING_CV: (
        Application.Status.CV_FOLLOWUP_1,
        Message.MessageType.CV_FOLLOWUP_1,
    ),
    Application.Status.CV_FOLLOWUP_1: (
        Application.Status.CV_FOLLOWUP_2,
        Message.MessageType.CV_FOLLOWUP_2,
    ),
    Application.Status.CV_FOLLOWUP_2: (
        Application.Status.CV_OVERDUE,
        None,  # No message sent — just mark overdue
    ),
}


@close_old_connections
def check_cv_followups() -> None:
    """
    Advance qualified applications that are waiting for a CV but have not
    responded within the position's follow-up interval.

    Status progression (qualified path only):
      AWAITING_CV   → (send follow-up 1) → CV_FOLLOWUP_1
      CV_FOLLOWUP_1 → (send follow-up 2) → CV_FOLLOWUP_2
      CV_FOLLOWUP_2 → (no message)       → CV_OVERDUE

    Timing: compares last sent Message.sent_at against
    Position.follow_up_interval_hours. Falls back to Application.updated_at
    if no sent message record exists.

    IMPORTANT: This job does NOT process not-qualified / rejected candidates.
    Spec § 6 — check_cv_followups, § 10 step 6.
    """
    now = timezone.now()

    pending_followup_apps = (
        Application.objects
        .filter(
            status__in=list(_FOLLOWUP_TRANSITIONS.keys()),
            qualified=True,          # Only qualified candidates — never rejected
            cv_received_at__isnull=True,
        )
        .select_related("candidate", "position")
    )

    advanced = 0
    for app in pending_followup_apps:
        interval_hours = app.position.follow_up_interval_hours
        last_sent_at = _get_last_sent_message_time(app)

        if last_sent_at is None:
            # Fallback: use when the application last changed status
            last_sent_at = app.updated_at

        due_at = last_sent_at + timedelta(hours=interval_hours)
        if now < due_at:
            continue  # Not yet due

        next_status, message_type = _FOLLOWUP_TRANSITIONS[app.status]

        with transaction.atomic():
            if message_type is not None:
                send_followup(app, message_type)

            app.status = next_status
            app.save(update_fields=["status", "updated_at"])

        logger.info(
            "check_cv_followups: application=%s → %s (message_type=%s)",
            app.pk,
            next_status,
            message_type,
        )
        advanced += 1

    if advanced:
        logger.info("check_cv_followups: advanced %s application(s)", advanced)


def _get_last_sent_message_time(application):
    """
    Return the sent_at of the most recent successfully sent Message for this
    application, or None if no sent message exists.
    """
    last_msg = (
        Message.objects
        .filter(application=application, status=Message.Status.SENT)
        .order_by("-sent_at")
        .values("sent_at")
        .first()
    )
    return last_msg["sent_at"] if last_msg else None


# ─────────────────────────────────────────────────────────────────────────────
# Job 4: close_stale_rejected  (every 24 hrs)
# ─────────────────────────────────────────────────────────────────────────────

@close_old_connections
def close_stale_rejected() -> None:
    """
    Silently close AWAITING_CV_REJECTED applications where the candidate has
    not sent a CV within Position.rejected_cv_timeout_days.

    No message is sent. The application is simply moved to CLOSED.
    Spec § 6 — close_stale_rejected, § 10 step 6b.
    """
    now = timezone.now()

    # Fetch candidates where the timeout may have elapsed.
    # We compare against updated_at (which is refreshed on every status change)
    # as the best available proxy for when the application entered this state.
    stale_apps = (
        Application.objects
        .filter(
            status=Application.Status.AWAITING_CV_REJECTED,
            cv_received_at__isnull=True,
        )
        .select_related("position")
    )

    to_close = []
    for app in stale_apps:
        timeout_days = app.position.rejected_cv_timeout_days
        deadline = app.updated_at + timedelta(days=timeout_days)
        if now >= deadline:
            to_close.append(app.pk)

    if not to_close:
        return

    closed_count = 0
    with transaction.atomic():
        closed_count = (
            Application.objects
            .filter(pk__in=to_close)
            .update(
                status=Application.Status.CLOSED,
                updated_at=now,
            )
        )

    logger.info(
        "close_stale_rejected: closed %s stale rejected application(s)",
        closed_count,
    )


# ─────────────────────────────────────────────────────────────────────────────
# Job 5: poll_cv_inbox  (every 15 min)
# ─────────────────────────────────────────────────────────────────────────────

@close_old_connections
def poll_cv_inbox() -> None:
    """
    Poll the configured Gmail inbox label for unread messages with attachments,
    download them, run each through the CV smart-matching pipeline, then move
    processed messages to the GMAIL_PROCESSED_LABEL.

    Spec § 6 — poll_cv_inbox, § 10 step 5.
    """
    if not settings.GMAIL_POLL_ENABLED:
        return

    from cvs.services import process_inbound_cv as cv_process_inbound
    from messaging.services import GmailService

    gmail = GmailService()

    inbox_label = settings.GMAIL_INBOX_LABEL
    processed_label = settings.GMAIL_PROCESSED_LABEL

    inbox_label_id = gmail.get_label_id(inbox_label)
    processed_label_id = gmail.get_label_id(processed_label)

    if not inbox_label_id:
        logger.warning("poll_cv_inbox: Gmail label '%s' not found — skipping", inbox_label)
        return

    messages = gmail.list_unread_with_attachments(inbox_label)
    if not messages:
        return

    processed = 0
    for msg in messages:
        for att in msg.get("attachments", []):
            try:
                cv_process_inbound(
                    channel="email",
                    sender=msg["sender"],
                    file_name=att["name"],
                    file_content=att["data"],
                    text_body=msg.get("body_snippet", ""),
                    subject=msg.get("subject", ""),
                )
            except Exception as exc:
                logger.error(
                    "poll_cv_inbox: CV processing failed for Gmail msg=%s att=%s: %s",
                    msg["id"], att["name"], exc, exc_info=True,
                )

        if processed_label_id:
            gmail.move_to_label(
                msg["id"],
                add_label=processed_label_id,
                remove_label=inbox_label_id,
            )
        processed += 1

    logger.info("poll_cv_inbox: processed %s email(s) with attachments", processed)


# ─────────────────────────────────────────────────────────────────────────────
# Shared helpers
# ─────────────────────────────────────────────────────────────────────────────

def _format_transcript(turns: list) -> str:
    """
    Convert ElevenLabs transcript turns into a formatted dialogue string.
    Handles 'message', 'content', or 'text' as the text field name.
    Spec § 9 — Transcript Format.
    """
    if not turns:
        return ""
    lines = []
    for turn in turns:
        role = (turn.get("role") or "").capitalize()
        text = (
            turn.get("message") or turn.get("content") or turn.get("text") or ""
        ).strip()
        if role and text:
            lines.append(f"{role}: {text}")
    return "\n\n".join(lines)
