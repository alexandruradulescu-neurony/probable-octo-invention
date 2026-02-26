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
from django.db.models import OuterRef, Subquery
from django.utils import timezone
from django_apscheduler.util import close_old_connections

from applications.models import Application, StatusChange
from applications.transitions import set_call_failed, set_closed, set_followup_status
from cvs.constants import AWAITING_CV_STATUSES
from calls.models import Call
from calls.services import ElevenLabsError, ElevenLabsService
from calls.utils import apply_call_result
from evaluations.services import trigger_evaluation
from messaging.models import CandidateReply, Message
from messaging.services import save_candidate_reply, send_followup
from positions.models import Position

logger = logging.getLogger(__name__)

# ── Constants ──────────────────────────────────────────────────────────────────

# A call is considered "stuck" if it has been in initiated/in_progress longer
# than this threshold without the webhook delivering a completion event.
STUCK_CALL_THRESHOLD_MINUTES = 15

# Batch calls never receive a polled conversation_id; escalate to CALL_FAILED after this
# extended window so the application can re-enter the retry flow.
BATCH_ORPHAN_THRESHOLD_MINUTES = 60

ELEVENLABS_BASE_URL = "https://api.elevenlabs.io"

# Endpoints tried in spec-order when polling for a stuck call's state.
# Spec § 9 — Fallback Polling
_POLL_ENDPOINT_TEMPLATES = [
    "/v1/convai/conversations/{id}",
    "/v1/convai/calls/{id}",
    "/v1/conversations/{id}",
    "/v1/calls/{id}",
]


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
    queued = list(
        Application.objects
        .filter(
            status=Application.Status.CALL_QUEUED,
            position__status=Position.Status.OPEN,
        )
        .select_related("candidate", "position")
    )

    eligible_for_batch = []
    for app in queued:
        if _is_within_calling_hours(app.position, current_hour):
            eligible_for_batch.append(app)
        else:
            logger.debug(
                "Skipping application=%s — outside calling hours (hour=%s, window=%s-%s)",
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
            eligible_by_pk = {app.pk: app for app in eligible_for_batch}
            queued_ids = set(
                Application.objects
                .filter(
                    pk__in=eligible_by_pk.keys(),
                    status=Application.Status.CALL_QUEUED,
                )
                .values_list("pk", flat=True)
            )
            for app_pk in queued_ids:
                try:
                    with transaction.atomic():
                        set_call_failed(
                            eligible_by_pk[app_pk],
                            note="Batch call submission failed",
                        )
                except Exception as exc:
                    logger.error(
                        "Failed to mark application=%s as CALL_FAILED after batch error: %s",
                        app_pk, exc, exc_info=True,
                    )

    # ── Queue 2: individual — scheduled callbacks whose time has arrived ───────
    callbacks = (
        Application.objects
        .filter(
            status=Application.Status.CALLBACK_SCHEDULED,
            callback_scheduled_at__lte=now,
            position__status=Position.Status.OPEN,
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
            set_call_failed(app, note="Call initiation failed in scheduler")


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

    stuck_calls = list(
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

    if not stuck_calls:
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

    # ── Orphaned batch calls: INITIATED with no conversation_id after extended threshold ──
    # Batch calls start with eleven_labs_conversation_id=NULL; if the webhook never fires
    # and the conversation_id is never bound, they cannot be polled.  Escalate to CALL_FAILED
    # after BATCH_ORPHAN_THRESHOLD_MINUTES so the application re-enters the retry flow.
    orphan_threshold = timezone.now() - timedelta(minutes=BATCH_ORPHAN_THRESHOLD_MINUTES)
    orphan_calls = list(
        Call.objects
        .filter(
            status=Call.Status.INITIATED,
            initiated_at__lt=orphan_threshold,
            eleven_labs_conversation_id__isnull=True,
            eleven_labs_batch_id__isnull=False,
        )
        .select_related("application__candidate", "application__position")
    )
    orphaned = 0
    for call in orphan_calls:
        try:
            with transaction.atomic():
                call.status = Call.Status.FAILED
                call.ended_at = timezone.now()
                call.save(update_fields=["status", "ended_at"])
                set_call_failed(
                    call.application,
                    note="Batch call orphaned — webhook never fired, conversation_id unbound",
                )
            orphaned += 1
            logger.warning(
                "sync_stuck_calls: orphaned batch call escalated to FAILED: "
                "call=%s batch_id=%s application=%s",
                call.pk, call.eleven_labs_batch_id, call.application_id,
            )
        except Exception as exc:
            logger.error(
                "sync_stuck_calls: failed to escalate orphaned call=%s: %s",
                call.pk, exc, exc_info=True,
            )

    if orphaned:
        logger.info("sync_stuck_calls: escalated %s orphaned batch call(s)", orphaned)


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
    call_status, is_completed = apply_call_result(call, data)

    logger.info(
        "sync_stuck_calls: updated call=%s status=%s application=%s",
        call.pk,
        call_status,
        call.application_id,
    )

    if is_completed:
        trigger_evaluation(call)


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

    latest_sent_msg = (
        Message.objects
        .filter(application=OuterRef("pk"), status=Message.Status.SENT)
        .order_by("-sent_at")
        .values("sent_at")[:1]
    )

    # Pre-annotate the most recent transition INTO an awaiting-CV status so the
    # fallback baseline is resolved in a single query instead of N+1 per app.
    latest_awaiting_cv_transition = (
        StatusChange.objects
        .filter(
            application=OuterRef("pk"),
            to_status__in=list(AWAITING_CV_STATUSES),
        )
        .order_by("-changed_at")
        .values("changed_at")[:1]
    )

    pending_followup_apps = (
        Application.objects
        .filter(
            status__in=list(_FOLLOWUP_TRANSITIONS.keys()),
            qualified=True,          # Only qualified candidates — never rejected
            cv_received_at__isnull=True,
        )
        .select_related("candidate", "position")
        .annotate(
            _last_sent_at=Subquery(latest_sent_msg),
            _status_change_at=Subquery(latest_awaiting_cv_transition),
        )
    )

    advanced = 0
    for app in pending_followup_apps:
        interval_hours = app.position.follow_up_interval_hours
        last_sent_at = app._last_sent_at

        if last_sent_at is None:
            last_sent_at = app._status_change_at

        if last_sent_at is None:
            last_sent_at = app.updated_at

        due_at = last_sent_at + timedelta(hours=interval_hours)
        if now < due_at:
            continue  # Not yet due

        next_status, message_type = _FOLLOWUP_TRANSITIONS[app.status]

        with transaction.atomic():
            if message_type is not None:
                send_followup(app, message_type)

            set_followup_status(
                app,
                next_status,
                note=f"Scheduler follow-up transition to {next_status}",
            )

        logger.info(
            "check_cv_followups: application=%s → %s (message_type=%s)",
            app.pk,
            next_status,
            message_type,
        )
        advanced += 1

    if advanced:
        logger.info("check_cv_followups: advanced %s application(s)", advanced)


# ─────────────────────────────────────────────────────────────────────────────
# Job 4: close_stale_rejected  (every 24 hrs)
# ─────────────────────────────────────────────────────────────────────────────

@close_old_connections
def close_stale_rejected() -> None:
    """
    Silently close rejected applications that have reached their end-of-life.
    No message is sent in either case. Spec § 6 — close_stale_rejected.

    Three cases are handled:

    1. AWAITING_CV_REJECTED (no CV received):
       Close when updated_at + rejected_cv_timeout_days has elapsed.
       The candidate never responded to the CV request.

    2. CV_RECEIVED_REJECTED (CV was received from a not-qualified candidate):
       Close when cv_received_at + rejected_cv_timeout_days has elapsed.
       The CV is on file; no further action is needed in the pipeline.

    3. CV_OVERDUE (qualified candidate never sent a CV after all follow-ups):
       Close when updated_at + rejected_cv_timeout_days has elapsed.
       All follow-up attempts have been exhausted.
    """
    now = timezone.now()
    to_close = []

    # Case 1: still waiting for CV — timed out
    awaiting = (
        Application.objects
        .filter(
            status=Application.Status.AWAITING_CV_REJECTED,
            cv_received_at__isnull=True,
        )
        .select_related("position")
    )
    for app in awaiting:
        transition_time = (
            StatusChange.objects
            .filter(
                application=app,
                to_status=Application.Status.AWAITING_CV_REJECTED,
            )
            .order_by("-changed_at")
            .values_list("changed_at", flat=True)
            .first()
        )
        baseline = transition_time or app.updated_at
        deadline = baseline + timedelta(days=app.position.rejected_cv_timeout_days)
        if now >= deadline:
            to_close.append((app, "Rejected CV timeout — no CV received"))

    # Case 2: CV received but candidate was not qualified — archive after same window
    received = (
        Application.objects
        .filter(
            status=Application.Status.CV_RECEIVED_REJECTED,
            cv_received_at__isnull=False,
        )
        .select_related("position")
    )
    for app in received:
        deadline = app.cv_received_at + timedelta(days=app.position.rejected_cv_timeout_days)
        if now >= deadline:
            to_close.append((app, "Rejected CV timeout — CV received, closing"))

    # Case 3: CV overdue — qualified candidate never sent a CV after all follow-ups
    cv_overdue = (
        Application.objects
        .filter(status=Application.Status.CV_OVERDUE)
        .select_related("position")
    )
    for app in cv_overdue:
        transition_time = (
            StatusChange.objects
            .filter(
                application=app,
                to_status=Application.Status.CV_OVERDUE,
            )
            .order_by("-changed_at")
            .values_list("changed_at", flat=True)
            .first()
        )
        baseline = transition_time or app.updated_at
        deadline = baseline + timedelta(days=app.position.rejected_cv_timeout_days)
        if now >= deadline:
            to_close.append((app, "CV overdue timeout — closing after all follow-ups exhausted"))

    if not to_close:
        return

    closed_count = 0
    for app, note in to_close:
        try:
            with transaction.atomic():
                set_closed(app, note=note)
                closed_count += 1
        except Exception as exc:
            logger.error(
                "close_stale_rejected: failed to close application=%s: %s",
                app.pk, exc, exc_info=True,
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
    # Check DB flag first (controlled via Settings page), fall back to env setting.
    try:
        from config.models import SystemSetting
        poll_enabled = SystemSetting.get_bool("gmail_poll_enabled", default=settings.GMAIL_POLL_ENABLED)
    except Exception:
        poll_enabled = settings.GMAIL_POLL_ENABLED

    if not poll_enabled:
        return

    _run_poll_cv_inbox()


def _run_poll_cv_inbox() -> dict:
    """
    Core Gmail CV-inbox polling logic — runs unconditionally (no poll_enabled guard).

    Fetches ALL unread messages (the inbox is dedicated to CV submissions).
    For each email:
      • If it contains file attachments → run each through the CV pipeline and
        move the message to GMAIL_PROCESSED_LABEL (or mark as read if no label).
      • If it has no attachments → mark as read so it is not picked up again.

    GMAIL_INBOX_LABEL is used as an optional scope filter; if the label does not
    exist in Gmail the poll falls back to querying all unread mail.

    Returns a diagnostic dict:
        label          — configured inbox label (may be None/empty)
        label_found    — whether the label was resolved in Gmail
        query_matches  — total unread messages fetched
        with_cv        — count that contained at least one file attachment
        skipped        — count that had no attachments (marked as read only)
        processed      — count whose attachments were ingested into the CV pipeline
    """
    from cvs.services import process_inbound_cv as cv_process_inbound
    from messaging.services import GmailService

    gmail = GmailService()

    inbox_label = settings.GMAIL_INBOX_LABEL or None
    processed_label = settings.GMAIL_PROCESSED_LABEL

    # Resolve label IDs — failures are non-fatal; we just widen the scope.
    inbox_label_id = gmail.get_label_id(inbox_label) if inbox_label else None
    processed_label_id = gmail.get_label_id(processed_label) if processed_label else None

    label_found = inbox_label_id is not None if inbox_label else True
    if inbox_label and not inbox_label_id:
        logger.warning(
            "poll_cv_inbox: Gmail label '%s' not found — polling all unread mail instead",
            inbox_label,
        )

    # Fetch all unread messages (scoped to label if it exists).
    effective_label = inbox_label if inbox_label_id else None
    messages, query_count = gmail.list_unread_messages(effective_label)

    with_cv = 0
    skipped = 0
    processed = 0

    for msg in messages:
        attachments = msg.get("attachments", [])
        sender = msg.get("sender", "")
        body_snippet = (msg.get("body_snippet") or "").strip()
        subject = (msg.get("subject") or "").strip()

        if attachments:
            with_cv += 1
            for att in attachments:
                try:
                    cv_process_inbound(
                        channel="email",
                        sender=sender,
                        file_name=att["name"],
                        file_content=att["data"],
                        text_body=body_snippet,
                        subject=subject,
                    )
                    processed += 1
                except Exception as exc:
                    logger.error(
                        "poll_cv_inbox: CV processing failed for Gmail msg=%s att=%s: %s",
                        msg["id"], att["name"], exc, exc_info=True,
                    )

            # If the email body has text alongside the CV, save it as a reply.
            if body_snippet:
                save_candidate_reply(
                    sender=sender,
                    channel="email",
                    body=body_snippet,
                    subject=subject,
                    external_id=msg["id"],
                )

            # Move to processed label (which also marks as read), or just mark as read.
            if processed_label_id:
                gmail.move_to_label(
                    msg["id"],
                    add_label=processed_label_id,
                    remove_label=inbox_label_id,
                )
            else:
                gmail.mark_as_read(msg["id"])

        else:
            # No attachment — if there is a text body, record it as a candidate reply.
            skipped += 1
            if body_snippet:
                save_candidate_reply(
                    sender=sender,
                    channel="email",
                    body=body_snippet,
                    subject=subject,
                    external_id=msg["id"],
                )
            gmail.mark_as_read(msg["id"])
            logger.debug(
                "poll_cv_inbox: no attachment in msg=%s from=%s subject=%r — marked as read",
                msg["id"], sender, subject,
            )

    logger.info(
        "poll_cv_inbox: %s unread fetched | %s had CV attachment(s) | %s skipped (no attachment)",
        query_count, with_cv, skipped,
    )
    return {
        "label": inbox_label,
        "label_found": label_found,
        "query_matches": query_count,
        "with_cv": with_cv,
        "skipped": skipped,
        "processed": processed,
    }


