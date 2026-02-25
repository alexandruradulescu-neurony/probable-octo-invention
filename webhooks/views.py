"""
webhooks/views.py

Inbound webhook endpoints.

  POST /webhooks/elevenlabs/   — ElevenLabs ConvAI post-call event
  POST /webhooks/whapi/        — Whapi inbound WhatsApp message

Both views are CSRF-exempt (external services cannot obtain a CSRF token).
Both validate a shared secret before any processing occurs.
"""

import hashlib
import hmac
import json
import logging
import time

import requests as http_requests
from django.conf import settings
from django.http import HttpResponse, JsonResponse
from django.utils import timezone
from django.utils.decorators import method_decorator
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST
from django.db import transaction

from applications.models import Application
from calls.models import Call
from cvs.services import process_inbound_cv as cv_process_inbound
from evaluations.services import ClaudeService, ClaudeServiceError

logger = logging.getLogger(__name__)

# ── Shared response helpers ────────────────────────────────────────────────────

def _ok(message: str = "ok") -> JsonResponse:
    return JsonResponse({"status": message}, status=200)


def _reject(reason: str, status: int = 401) -> JsonResponse:
    logger.warning("Webhook rejected: %s", reason)
    return JsonResponse({"error": reason}, status=status)


# ─────────────────────────────────────────────────────────────────────────────
# ElevenLabs webhook
# ─────────────────────────────────────────────────────────────────────────────

# Maximum age of a signed webhook before we reject it (prevents replay attacks).
_ELEVENLABS_TIMESTAMP_TOLERANCE_SECS = 300  # 5 minutes


@csrf_exempt
@require_POST
def elevenlabs_webhook(request):
    """
    POST /webhooks/elevenlabs/

    Receives the post-call event from ElevenLabs ConvAI.
    Spec § 8, § 9.

    Expected payload (ElevenLabs ConvAI v1):
      {
        "type": "post_call_transcription",
        "data": {
          "conversation_id": "conv_...",
          "status": "done | failed | no_answer",
          "transcript": [{"role": "agent|user", "message": "..."}],
          "analysis": {
            "transcript_summary": "...",
            "call_summary_title": "..."
          },
          "metadata": {"call_duration_secs": 120, ...},
          "recording_url": "..."
        }
      }

    The `conversation_id` may also appear at the top level.
    """
    raw_body = request.body

    # ── 1. Signature validation ────────────────────────────────────────────────
    secret = settings.ELEVENLABS_WEBHOOK_SECRET
    if secret:
        sig_header = request.META.get("HTTP_ELEVENLABS_SIGNATURE", "")
        if not sig_header:
            return _reject("Missing ElevenLabs-Signature header")

        validation_error = _validate_elevenlabs_signature(
            sig_header, raw_body, secret
        )
        if validation_error:
            return _reject(validation_error)
    else:
        logger.warning(
            "ELEVENLABS_WEBHOOK_SECRET is not set — skipping signature validation."
        )

    # ── 2. Parse body ──────────────────────────────────────────────────────────
    try:
        payload = json.loads(raw_body)
    except json.JSONDecodeError:
        return _reject("Invalid JSON body", status=400)

    # ── 3. Extract fields (support both flat and nested `data` layouts) ────────
    data = payload.get("data", payload)  # fall back to root if no `data` key

    conversation_id = (
        data.get("conversation_id")
        or payload.get("conversation_id")
    )
    if not conversation_id:
        logger.error("ElevenLabs webhook missing conversation_id: %s", payload)
        return _ok("no_conversation_id")

    raw_status = (data.get("status") or "").lower()
    transcript_turns = data.get("transcript") or []
    analysis = data.get("analysis") or {}
    metadata = data.get("metadata") or {}
    recording_url = data.get("recording_url") or None

    # ── 4. Locate the Call record ──────────────────────────────────────────────
    try:
        call = Call.objects.select_related(
            "application__candidate",
            "application__position",
        ).get(eleven_labs_conversation_id=conversation_id)
    except Call.DoesNotExist:
        # The call may have originated from the batch API, in which case the
        # conversation_id was not known at submission time.  Attempt late-binding
        # via the application.pk embedded in the webhook payload's user_id.
        call = _bind_batch_call(payload, data, conversation_id)
        if call is None:
            logger.warning(
                "ElevenLabs webhook received for unknown conversation_id=%s "
                "(batch lookup also failed)",
                conversation_id,
            )
            return _ok("call_not_found")

    # ── 5. Map ElevenLabs status → Call.Status ─────────────────────────────────
    call_status = _map_elevenlabs_status(raw_status)
    is_completed = call_status == Call.Status.COMPLETED

    # ── 6. Format transcript ───────────────────────────────────────────────────
    formatted_transcript = _format_transcript(transcript_turns)

    # ── 7. Persist call data ───────────────────────────────────────────────────
    update_fields = ["status", "updated_at"] if hasattr(call, "updated_at") else ["status"]
    call.status = call_status
    if formatted_transcript:
        call.transcript = formatted_transcript
        update_fields.append("transcript")
    if analysis.get("transcript_summary"):
        call.summary = analysis["transcript_summary"]
        update_fields.append("summary")
    if analysis.get("call_summary_title"):
        call.summary_title = analysis["call_summary_title"]
        update_fields.append("summary_title")
    if recording_url:
        call.recording_url = recording_url
        update_fields.append("recording_url")
    duration = metadata.get("call_duration_secs") or data.get("duration_seconds")
    if duration is not None:
        call.duration_seconds = int(duration)
        update_fields.append("duration_seconds")
    if is_completed or call_status in (Call.Status.FAILED, Call.Status.NO_ANSWER, Call.Status.BUSY):
        call.ended_at = timezone.now()
        update_fields.append("ended_at")

    # Deduplicate update_fields list while preserving order
    seen = set()
    unique_fields = []
    for f in update_fields:
        if f not in seen:
            seen.add(f)
            unique_fields.append(f)

    with transaction.atomic():
        # Use save() without update_fields to avoid missing 'updated_at' issues
        call.save()

        application = call.application

        if is_completed:
            application.status = Application.Status.CALL_COMPLETED
            application.save(update_fields=["status", "updated_at"])

            # Immediately advance to scoring before triggering Claude
            application.status = Application.Status.SCORING
            application.save(update_fields=["status", "updated_at"])

        elif call_status in (Call.Status.FAILED, Call.Status.NO_ANSWER, Call.Status.BUSY):
            application.status = Application.Status.CALL_FAILED
            application.save(update_fields=["status", "updated_at"])

    logger.info(
        "ElevenLabs webhook processed: conversation_id=%s call_status=%s is_completed=%s",
        conversation_id,
        call_status,
        is_completed,
    )

    # ── 8. Trigger Claude evaluation for completed calls ───────────────────────
    if is_completed:
        _trigger_evaluation(call)

    return _ok()


def _trigger_evaluation(call: Call) -> None:
    """
    Run Claude's evaluation synchronously.
    Errors are caught and logged — we never let evaluation failures cause
    the webhook to re-deliver (which would duplicate the call record update).
    """
    try:
        service = ClaudeService()
        evaluation = service.evaluate_call(call)
        logger.info(
            "Claude evaluation complete: evaluation=%s outcome=%s application=%s",
            evaluation.pk,
            evaluation.outcome,
            call.application_id,
        )
    except ClaudeServiceError as exc:
        logger.error(
            "Claude evaluation failed for call=%s: %s",
            call.pk,
            exc,
            exc_info=True,
        )
    except Exception as exc:  # noqa: BLE001
        logger.error(
            "Unexpected error during Claude evaluation for call=%s: %s",
            call.pk,
            exc,
            exc_info=True,
        )


# ── ElevenLabs helpers ─────────────────────────────────────────────────────────

def _validate_elevenlabs_signature(
    sig_header: str, body: bytes, secret: str
) -> str | None:
    """
    Validate an ElevenLabs HMAC-SHA256 webhook signature.

    Header format:  ElevenLabs-Signature: t={unix_timestamp},v0={hmac_hex}
    Signed message: "{timestamp}.{raw_body}"

    Returns None on success, or an error string on failure.
    """
    try:
        parts = dict(part.split("=", 1) for part in sig_header.split(","))
        timestamp_str = parts.get("t", "")
        received_sig = parts.get("v0", "")
    except (ValueError, AttributeError):
        return "Malformed ElevenLabs-Signature header"

    if not timestamp_str or not received_sig:
        return "ElevenLabs-Signature header missing t= or v0= component"

    try:
        timestamp = int(timestamp_str)
    except ValueError:
        return "ElevenLabs-Signature timestamp is not an integer"

    age = int(time.time()) - timestamp
    if abs(age) > _ELEVENLABS_TIMESTAMP_TOLERANCE_SECS:
        return f"ElevenLabs-Signature timestamp is too old (age={age}s)"

    signed_payload = f"{timestamp_str}.".encode() + body
    expected_sig = hmac.new(
        secret.encode(),
        signed_payload,
        hashlib.sha256,
    ).hexdigest()

    if not hmac.compare_digest(expected_sig, received_sig):
        return "ElevenLabs-Signature HMAC mismatch"

    return None


def _map_elevenlabs_status(raw_status: str) -> str:
    """
    Map an ElevenLabs call status string to the internal Call.Status value.
    Spec § 9 — Status mapping.
    """
    mapping = {
        "done": Call.Status.COMPLETED,
        "completed": Call.Status.COMPLETED,
        "failed": Call.Status.FAILED,
        "no_answer": Call.Status.NO_ANSWER,
        "busy": Call.Status.BUSY,
        "in_progress": Call.Status.IN_PROGRESS,
        "processing": Call.Status.IN_PROGRESS,
    }
    return mapping.get(raw_status, Call.Status.IN_PROGRESS)


def _bind_batch_call(
    payload: dict, data: dict, conversation_id: str
) -> "Call | None":
    """
    Attempt to late-bind a `conversation_id` to a batch-initiated Call record.

    When calls are submitted via the ElevenLabs batch API the `conversation_id`
    is not known until ElevenLabs fires the per-call webhook.  To link the webhook
    back to the correct Call record we embed the `application.pk` as `user_id`
    inside `conversation_initiation_client_data` at submission time.

    This function:
      1. Extracts `user_id` from the webhook payload.
      2. Finds the most recent INITIATED Call for that application whose
         `eleven_labs_conversation_id` is still NULL (i.e. not yet bound).
      3. Atomically binds the `conversation_id` to that Call and returns it,
         fully loaded with related objects for further processing.

    A `select_for_update()` lock prevents two concurrent webhook deliveries
    (rare but possible) from binding the same Call twice.

    Returns the bound Call, or None if the lookup fails.
    """
    app_id = _extract_batch_application_id(payload, data)
    if not app_id:
        return None

    try:
        with transaction.atomic():
            call = (
                Call.objects
                .select_for_update()
                .select_related("application__candidate", "application__position")
                .filter(
                    application_id=app_id,
                    status=Call.Status.INITIATED,
                    eleven_labs_conversation_id__isnull=True,
                )
                .order_by("-initiated_at")
                .first()
            )
            if call is None:
                logger.warning(
                    "Batch call lookup: no unbound INITIATED Call for application_id=%s",
                    app_id,
                )
                return None

            call.eleven_labs_conversation_id = conversation_id
            call.save(update_fields=["eleven_labs_conversation_id"])

        logger.info(
            "Batch call late-bound: application_id=%s call=%s conversation_id=%s",
            app_id,
            call.pk,
            conversation_id,
        )
        return call

    except Exception as exc:  # noqa: BLE001
        logger.error(
            "Batch call binding failed for application_id=%s conversation_id=%s: %s",
            app_id,
            conversation_id,
            exc,
            exc_info=True,
        )
        return None


def _extract_batch_application_id(payload: dict, data: dict) -> "str | None":
    """
    Extract the application PK that was embedded at batch-submission time.

    ElevenLabs echoes `conversation_initiation_client_data` back in the webhook
    payload.  We look in both `data` and the root `payload` (some API versions
    nest it differently).
    """
    for container in (data, payload):
        client_data = container.get("conversation_initiation_client_data") or {}
        user_id = client_data.get("user_id")
        if user_id:
            return str(user_id)
    return None


def _format_transcript(turns: list) -> str:
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


# ─────────────────────────────────────────────────────────────────────────────
# Whapi webhook
# ─────────────────────────────────────────────────────────────────────────────

# Media message types that may contain a CV attachment.
_WHAPI_MEDIA_TYPES = frozenset(
    {"image", "document", "audio", "video", "sticker", "file"}
)


@csrf_exempt
@require_POST
def whapi_webhook(request):
    """
    POST /webhooks/whapi/

    Receives inbound WhatsApp messages from Whapi.
    Spec § 8, § 10.

    Expected payload:
      {
        "messages": [
          {
            "id": "...",
            "from": "1234567890@s.whatsapp.net",
            "type": "text | image | document | audio | video",
            "body": "...",
            "media": {
              "id": "...",
              "url": "https://...",
              "mime_type": "application/pdf"
            }
          }
        ]
      }
    """
    raw_body = request.body

    # ── 1. Token validation ────────────────────────────────────────────────────
    secret = settings.WHAPI_WEBHOOK_SECRET
    if secret:
        if not _validate_whapi_token(request, secret):
            return _reject("Invalid or missing Whapi token")
    else:
        logger.warning(
            "WHAPI_WEBHOOK_SECRET is not set — skipping token validation."
        )

    # ── 2. Parse body ──────────────────────────────────────────────────────────
    try:
        payload = json.loads(raw_body)
    except json.JSONDecodeError:
        return _reject("Invalid JSON body", status=400)

    messages = payload.get("messages") or []
    if not messages:
        # Whapi sends other event types (status updates, etc.) — acknowledge silently.
        return _ok("no_messages")

    # ── 3. Process each inbound message ───────────────────────────────────────
    for msg in messages:
        _handle_whapi_message(msg)

    return _ok()


def _validate_whapi_token(request, secret: str) -> bool:
    """
    Validate the Whapi webhook token.

    Whapi sends the token as either:
      Authorization: Bearer {token}
    or a dedicated header:
      X-Whapi-Token: {token}
    """
    # Check X-Whapi-Token header first
    token = request.META.get("HTTP_X_WHAPI_TOKEN", "")
    if token:
        return hmac.compare_digest(token, secret)

    # Fall back to Authorization: Bearer {token}
    auth_header = request.META.get("HTTP_AUTHORIZATION", "")
    if auth_header.lower().startswith("bearer "):
        token = auth_header[7:].strip()
        return hmac.compare_digest(token, secret)

    return False


def _handle_whapi_message(msg: dict) -> None:
    """
    Process a single Whapi message object.
    Downloads media and delegates to the CV smart-matching service.
    """
    msg_type = (msg.get("type") or "").lower()
    sender_raw = msg.get("from") or ""

    sender = sender_raw.split("@")[0] if "@" in sender_raw else sender_raw

    if msg_type in _WHAPI_MEDIA_TYPES:
        media = msg.get("media") or {}
        media_url = media.get("url") or media.get("link") or ""
        file_name = media.get("filename") or media.get("file_name") or "attachment"
        text = (msg.get("body") or msg.get("caption") or "").strip()

        logger.info(
            "Whapi inbound media message: sender=%s type=%s media_url=%s",
            sender,
            msg_type,
            media_url[:80] if media_url else "(none)",
        )

        if not media_url:
            logger.warning("Whapi media message has no URL — skipping")
            return

        file_content = _download_whapi_media(media_url)
        if file_content is None:
            return

        try:
            cv_process_inbound(
                channel="whatsapp",
                sender=sender,
                file_name=file_name,
                file_content=file_content,
                text_body=text,
                raw_payload=msg,
            )
        except Exception as exc:
            logger.error(
                "CV processing failed for WhatsApp sender=%s: %s",
                sender, exc, exc_info=True,
            )

    elif msg_type == "text":
        logger.debug(
            "Whapi inbound text message from sender=%s (no action)", sender
        )


def _download_whapi_media(url: str) -> bytes | None:
    """Download a media file from Whapi. Returns raw bytes or None on failure."""
    try:
        resp = http_requests.get(url, timeout=30)
        resp.raise_for_status()
        return resp.content
    except http_requests.RequestException as exc:
        logger.error("Failed to download Whapi media from %s: %s", url, exc)
        return None
