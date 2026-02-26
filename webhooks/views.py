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
from urllib.parse import urlparse

import requests as http_requests
from django.conf import settings
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST
from django.db import transaction

from calls.models import Call
from calls.utils import apply_call_result
from candidates.services import lookup_candidate_by_email, lookup_candidate_by_phone
from cvs.services import process_inbound_cv as cv_process_inbound
from evaluations.services import trigger_evaluation
from messaging.models import CandidateReply
from messaging.services import save_candidate_reply

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
    elif not settings.DEBUG:
        logger.error("ELEVENLABS_WEBHOOK_SECRET is not configured in production.")
        return JsonResponse({"error": "server_misconfigured"}, status=500)
    else:
        logger.warning(
            "ELEVENLABS_WEBHOOK_SECRET is not set — skipping signature validation (DEBUG mode)."
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

    # ── 5. Apply call result and advance Application status ──────────────────
    call_status, is_completed = apply_call_result(call, data)

    logger.info(
        "ElevenLabs webhook processed: conversation_id=%s call_status=%s is_completed=%s",
        conversation_id,
        call_status,
        is_completed,
    )

    # ── 6. Trigger Claude evaluation for completed calls ───────────────────────
    if is_completed:
        trigger_evaluation(call)

    return _ok()


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
    elif not settings.DEBUG:
        logger.error("WHAPI_WEBHOOK_SECRET is not configured in production.")
        return JsonResponse({"error": "server_misconfigured"}, status=500)
    else:
        logger.warning(
            "WHAPI_WEBHOOK_SECRET is not set — skipping token validation (DEBUG mode)."
        )

    # ── 2. Parse body ──────────────────────────────────────────────────────────
    try:
        payload = json.loads(raw_body)
    except json.JSONDecodeError:
        return _reject("Invalid JSON body", status=400)

    messages = payload.get("messages") or []
    if not messages:
        logger.debug("Whapi webhook: no messages in payload. keys=%s", list(payload.keys()))
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


def _extract_whapi_text(msg: dict, msg_type: str) -> str:
    """
    Extract the plain-text body from a Whapi message object.

    Whapi nests the text under a type-specific key:
      text messages  → msg["text"]["body"]
      media captions → msg["caption"] or msg[msg_type]["caption"]
    Falls back to the top-level "body" key for forward-compatibility.
    """
    # Text messages
    text_obj = msg.get("text")
    if isinstance(text_obj, dict):
        body = (text_obj.get("body") or "").strip()
        if body:
            return body

    # Media captions: top-level "caption" or nested under the type key
    caption = msg.get("caption") or ""
    if not caption:
        type_obj = msg.get(msg_type) or {}
        caption = (type_obj.get("caption") or "") if isinstance(type_obj, dict) else ""

    # Final fallback
    return (caption or msg.get("body") or "").strip()


def _handle_whapi_message(msg: dict) -> None:
    """
    Process a single Whapi message object.
    Downloads media and delegates to the CV smart-matching service.

    Whapi stores media data under a key matching the message type
    (e.g. msg["document"], msg["image"]) rather than a generic "media" key.
    We fall back to "media" for forward-compatibility.
    """
    msg_type = (msg.get("type") or "").lower()
    sender_raw = msg.get("from") or ""

    # Skip outbound messages (fromMe=True means WE sent it)
    if msg.get("from_me") or msg.get("fromMe"):
        return

    sender = sender_raw.split("@")[0] if "@" in sender_raw else sender_raw

    if msg_type in _WHAPI_MEDIA_TYPES:
        # Whapi puts media info under the type-specific key (e.g. "document"),
        # with a fallback to the generic "media" key.
        media = msg.get(msg_type) or msg.get("media") or {}
        media_url = media.get("link") or media.get("url") or ""
        file_name = (
            media.get("file_name")
            or media.get("filename")
            or media.get("name")
            or f"attachment.{msg_type}"
        )
        text = _extract_whapi_text(msg, msg_type)

        logger.info(
            "Whapi inbound media message: sender=%s type=%s file=%s media_url=%s",
            sender,
            msg_type,
            file_name,
            media_url[:80] if media_url else "(none)",
        )

        if not media_url:
            logger.warning(
                "Whapi media message has no URL — skipping. msg_type=%s keys=%s",
                msg_type, list(msg.keys()),
            )
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

        # Persist any caption text accompanying the document as a CandidateReply.
        if text:
            save_candidate_reply(
                sender=sender,
                channel="whatsapp",
                body=text,
                external_id=msg.get("id"),
            )

    elif msg_type == "text":
        body = _extract_whapi_text(msg, msg_type)
        if body:
            save_candidate_reply(
                sender=sender,
                channel="whatsapp",
                body=body,
                external_id=msg.get("id"),
            )
        else:
            logger.debug("Whapi inbound empty text from sender=%s — skipping", sender)


def _download_whapi_media(url: str) -> bytes | None:
    """
    Download a media file from Whapi. Returns raw bytes or None on failure.
    The WHAPI_TOKEN is included as a Bearer token — required for authenticated
    media endpoints on most Whapi plans.
    """
    if urlparse(url).scheme != "https":
        logger.warning("Rejected non-HTTPS Whapi media URL: %s", url[:100])
        return None

    headers = {}
    token = settings.WHAPI_TOKEN
    if token:
        headers["Authorization"] = f"Bearer {token}"

    try:
        resp = http_requests.get(url, headers=headers, timeout=30)
        resp.raise_for_status()
        return resp.content
    except http_requests.RequestException as exc:
        logger.error("Failed to download Whapi media from %s: %s", url, exc)
        return None
