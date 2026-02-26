"""
messaging/services.py

Outbound messaging: WhatsApp (Whapi) and Email (Gmail API).

Public orchestrators:
  send_cv_request(application, qualified)  — post-evaluation CV request
  send_followup(application, message_type) — timed follow-up for qualified candidates
"""

import base64
import logging
from email.mime.text import MIMEText

import requests as http_requests
from django.conf import settings
from django.utils import timezone

from applications.models import Application
from applications.transitions import set_awaiting_cv
from messaging.models import Message, MessageTemplate

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# WhapiService
# ─────────────────────────────────────────────────────────────────────────────

class WhapiService:
    """Send WhatsApp messages via the Whapi REST API."""

    def __init__(self):
        self.token = settings.WHAPI_TOKEN
        self.base_url = (settings.WHAPI_API_URL or "").rstrip("/")

    def send_text(self, phone: str, body: str) -> tuple[bool, str | None]:
        """
        Send a plain-text WhatsApp message.

        Args:
            phone: Recipient phone in E.164 digits (e.g. "40712345678").
            body:  Message text.

        Returns:
            (success, external_id) — success is True whenever the HTTP call
            succeeds (2xx), regardless of whether Whapi returns a usable ID.
            external_id may be None even on success if the response omits it.
        """
        if not self.token or not self.base_url:
            logger.warning("Whapi credentials not configured — message not sent")
            return False, None

        url = f"{self.base_url}/messages/text"
        # Whapi JID format requires digits only — strip any leading '+'.
        jid_number = phone.lstrip("+")
        payload = {
            "to": f"{jid_number}@s.whatsapp.net",
            "body": body,
        }
        headers = {
            "Authorization": f"Bearer {self.token}",
            "Content-Type": "application/json",
        }

        try:
            resp = http_requests.post(url, json=payload, headers=headers, timeout=20)
            resp.raise_for_status()
            data = resp.json()
            # Whapi may return the ID under several key names depending on version.
            msg_id = (
                data.get("message_id")
                or data.get("id")
                or (data.get("message") or {}).get("id")
                or (data.get("messages") or [{}])[0].get("id")
            ) or None
            logger.info("WhatsApp sent to %s: external_id=%s", phone, msg_id)
            return True, msg_id
        except http_requests.RequestException as exc:
            logger.error("Whapi send failed to %s: %s", phone, exc)
            return False, None


# ─────────────────────────────────────────────────────────────────────────────
# GmailService
# ─────────────────────────────────────────────────────────────────────────────

class GmailService:
    """
    Send emails and poll inbox via Gmail API using OAuth2 refresh tokens.

    Requires google-api-python-client + google-auth-oauthlib.
    """

    def __init__(self):
        self._service = None

    @property
    def service(self):
        if self._service is None:
            self._service = self._build_service()
        return self._service

    @staticmethod
    def _build_service():
        from google.auth.transport.requests import Request
        from google.oauth2.credentials import Credentials
        from googleapiclient.discovery import build

        client_id = settings.GOOGLE_CLIENT_ID
        client_secret = settings.GOOGLE_CLIENT_SECRET

        # Prefer DB-stored OAuth credential (connected via Settings page) over env token.
        try:
            from config.models import OAuthCredential
            db_cred = OAuthCredential.objects.first()
        except Exception:
            db_cred = None

        refresh_token = db_cred.refresh_token if db_cred else settings.GOOGLE_REFRESH_TOKEN

        if not all([client_id, client_secret, refresh_token]):
            raise RuntimeError("Gmail API credentials not configured (GOOGLE_CLIENT_ID/SECRET/REFRESH_TOKEN).")

        creds = Credentials(
            token=None,
            refresh_token=refresh_token,
            token_uri="https://oauth2.googleapis.com/token",
            client_id=client_id,
            client_secret=client_secret,
            scopes=["https://mail.google.com/"],
        )
        creds.refresh(Request())
        return build("gmail", "v1", credentials=creds, cache_discovery=False)

    def _reset_service(self) -> None:
        """Clear cached service to force credential rebuild on next access."""
        self._service = None

    def send_email(self, to: str, subject: str, body: str) -> str | None:
        """
        Send a plain-text email via Gmail API.

        Returns:
            Gmail message ID on success, or None on failure.
        """
        try:
            svc = self.service
        except Exception as exc:
            logger.error("Gmail service init failed: %s", exc)
            return None

        mime = MIMEText(body, "plain", "utf-8")
        mime["To"] = to
        mime["Subject"] = subject

        raw = base64.urlsafe_b64encode(mime.as_bytes()).decode("ascii")

        for attempt in range(2):
            try:
                result = svc.users().messages().send(
                    userId="me",
                    body={"raw": raw},
                ).execute()
                msg_id = result.get("id")
                logger.info("Gmail sent to %s: id=%s", to, msg_id)
                return msg_id
            except Exception as exc:
                if attempt == 0 and "401" in str(exc):
                    logger.warning("Gmail auth error, rebuilding service: %s", exc)
                    self._reset_service()
                    try:
                        svc = self.service
                    except Exception:
                        logger.error("Gmail service rebuild failed after 401")
                        return None
                    continue
                logger.error("Gmail send failed to %s: %s", to, exc)
                return None
        return None

    def list_unread_messages(self, label: str | None = None) -> tuple[list[dict], int]:
        """
        Fetch all unread messages, optionally scoped to a Gmail label.

        Unlike the previous approach, this does NOT filter by has:attachment —
        every unread email is returned so the caller can decide what to do with
        each one (process CV if attachment found, mark-as-read otherwise).

        Returns:
            (messages, query_count) where:
              messages     — list of dicts: {id, sender, subject, body_snippet,
                             attachments: [{name, data}]}  — attachments may be []
              query_count  — total unread messages the query matched
        """
        try:
            svc = self.service
        except Exception as exc:
            logger.error("Gmail service init failed: %s", exc)
            return [], 0

        message_ids = None
        for attempt in range(2):
            try:
                query = f"label:{label} is:unread" if label else "is:unread"
                result = svc.users().messages().list(
                    userId="me", q=query, maxResults=50
                ).execute()
                message_ids = [m["id"] for m in result.get("messages", [])]
                break
            except Exception as exc:
                if attempt == 0 and "401" in str(exc):
                    logger.warning("Gmail auth error on list, rebuilding service: %s", exc)
                    self._reset_service()
                    try:
                        svc = self.service
                    except Exception:
                        logger.error("Gmail service rebuild failed after 401")
                        return [], 0
                    continue
                logger.error("Gmail list failed: %s", exc)
                return [], 0

        if message_ids is None:
            return [], 0

        query_count = len(message_ids)
        messages = []
        for mid in message_ids:
            try:
                msg_data = self._fetch_message(svc, mid)
                messages.append(msg_data)
            except Exception as exc:
                logger.warning("Failed to fetch Gmail message %s: %s", mid, exc)

        return messages, query_count

    def mark_as_read(self, message_id: str) -> None:
        """Remove the UNREAD system label from a message."""
        try:
            self.service.users().messages().modify(
                userId="me",
                id=message_id,
                body={"removeLabelIds": ["UNREAD"]},
            ).execute()
        except Exception as exc:
            logger.warning("Gmail mark-as-read failed for %s: %s", message_id, exc)

    @staticmethod
    def _fetch_message(svc, message_id: str) -> dict:
        """
        Fetch a single Gmail message and download any file attachments.

        Always returns a dict (attachments list may be empty).
        Walks the full MIME tree recursively so attachments nested inside
        multipart/related or multipart/alternative wrappers are not missed.
        """
        msg = svc.users().messages().get(
            userId="me", id=message_id, format="full"
        ).execute()

        headers = {h["name"].lower(): h["value"] for h in msg.get("payload", {}).get("headers", [])}
        sender = headers.get("from", "")
        subject = headers.get("subject", "")
        snippet = msg.get("snippet", "")

        attachment_parts = GmailService._collect_attachment_parts(msg.get("payload", {}))

        attachments = []
        for part in attachment_parts:
            att = svc.users().messages().attachments().get(
                userId="me", messageId=message_id, id=part["att_id"]
            ).execute()
            data = base64.urlsafe_b64decode(att["data"])
            attachments.append({"name": part["filename"], "data": data})

        return {
            "id": message_id,
            "sender": sender,
            "subject": subject,
            "body_snippet": snippet,
            "attachments": attachments,
        }

    @staticmethod
    def _collect_attachment_parts(part: dict) -> list[dict]:
        """
        Recursively walk a MIME part tree and return all parts that have a
        non-empty filename and an attachmentId (i.e. real file attachments,
        not inline body text or embedded images without a name).
        """
        results = []
        filename = part.get("filename", "")
        att_id = part.get("body", {}).get("attachmentId")
        if filename and att_id:
            results.append({"filename": filename, "att_id": att_id})
        for child in part.get("parts", []):
            results.extend(GmailService._collect_attachment_parts(child))
        return results

    def move_to_label(self, message_id: str, add_label: str, remove_label: str | None = None) -> None:
        """Add a label (and optionally remove another) from a Gmail message."""
        try:
            svc = self.service
            body = {"addLabelIds": [add_label]}
            if remove_label:
                body["removeLabelIds"] = [remove_label]
            svc.users().messages().modify(
                userId="me", id=message_id, body=body
            ).execute()
        except Exception as exc:
            logger.warning("Gmail label move failed for %s: %s", message_id, exc)

    def get_label_id(self, label_name: str) -> str | None:
        """Resolve a human-readable label name to its Gmail label ID."""
        try:
            svc = self.service
            labels = svc.users().labels().list(userId="me").execute().get("labels", [])
            for lbl in labels:
                if lbl["name"].lower() == label_name.lower():
                    return lbl["id"]
        except Exception as exc:
            logger.warning("Gmail label lookup failed for '%s': %s", label_name, exc)
        return None


# ─────────────────────────────────────────────────────────────────────────────
# Message body resolution
# ─────────────────────────────────────────────────────────────────────────────

# Hardcoded fallback bodies — used only when no active MessageTemplate exists
# for a given message_type × channel combination.
_FALLBACK_BODIES: dict[tuple[str, str], str] = {
    (Message.MessageType.CV_REQUEST, Message.Channel.WHATSAPP): (
        "Hi {first_name},\n\nGreat news! Following your recent call about the "
        "{position_title} position, we'd like to move forward.\n\n"
        "Please send us your CV at your earliest convenience.\n\n"
        "Your application reference is #{application_pk}.\n\nThank you!"
    ),
    (Message.MessageType.CV_REQUEST, Message.Channel.EMAIL): (
        "Hi {first_name},\n\nGreat news! Following your recent call about the "
        "{position_title} position, we'd like to move forward with your application.\n\n"
        "Could you please send us your CV/resume at your earliest convenience?\n\n"
        "Your application reference is #{application_pk}.\n\n"
        "Thank you!\nThe {position_title} Recruitment Team"
    ),
    (Message.MessageType.CV_REQUEST_REJECTED, Message.Channel.WHATSAPP): (
        "Hi {first_name},\n\nThank you for your interest in the {position_title} position. "
        "While this role may not be the best fit right now, we'd love to keep your details "
        "on file. Feel free to send us your CV!\n\nBest regards!"
    ),
    (Message.MessageType.CV_REQUEST_REJECTED, Message.Channel.EMAIL): (
        "Hi {first_name},\n\nThank you for your interest in the {position_title} position "
        "and for taking the time to speak with us.\n\n"
        "While this particular role may not be the best fit right now, we'd love to keep "
        "your details on file. If you'd like, please send us your CV/resume.\n\n"
        "Best regards,\nThe Recruitment Team"
    ),
    (Message.MessageType.CV_FOLLOWUP_1, Message.Channel.WHATSAPP): (
        "Hi {first_name}, just a gentle reminder — we're still waiting for your CV "
        "for the {position_title} role. Please send it at your earliest convenience."
    ),
    (Message.MessageType.CV_FOLLOWUP_1, Message.Channel.EMAIL): (
        "Hi {first_name},\n\nJust a gentle reminder that we're still waiting for your CV "
        "for the {position_title} role.\n\nPlease send it at your earliest convenience.\n\n"
        "Best regards,\nThe Recruitment Team"
    ),
    (Message.MessageType.CV_FOLLOWUP_2, Message.Channel.WHATSAPP): (
        "Hi {first_name}, this is a final reminder regarding your CV for the "
        "{position_title} position. Please send it as soon as possible so we can "
        "continue with your application."
    ),
    (Message.MessageType.CV_FOLLOWUP_2, Message.Channel.EMAIL): (
        "Hi {first_name},\n\nThis is a final reminder regarding your CV for the "
        "{position_title} position.\n\nPlease send it as soon as possible so we can "
        "continue processing your application.\n\nBest regards,\nThe Recruitment Team"
    ),
}

_FALLBACK_SUBJECTS: dict[str, str] = {
    Message.MessageType.CV_REQUEST:          "CV Request — {position_title}",
    Message.MessageType.CV_REQUEST_REJECTED: "Thank you — {position_title}",
    Message.MessageType.CV_FOLLOWUP_1:       "Reminder: CV for {position_title}",
    Message.MessageType.CV_FOLLOWUP_2:       "Final Reminder: CV for {position_title}",
    Message.MessageType.REJECTION:           "Your application — {position_title}",
}


def _resolve_message(
    message_type: str,
    channel: str,
    *,
    first_name: str,
    position_title: str,
    application_pk: int,
) -> tuple[str, str]:
    """
    Return (subject, body) for a given message_type × channel combination.

    Priority:
      1. Active MessageTemplate from the database (user-customised)
      2. Hardcoded fallback from _FALLBACK_BODIES

    Placeholders in both DB templates and fallbacks are resolved identically.
    """
    ctx = {
        "first_name":      first_name,
        "position_title":  position_title,
        "application_pk":  str(application_pk),
    }

    tpl = MessageTemplate.objects.filter(
        message_type=message_type,
        channel=channel,
        is_active=True,
    ).first()

    if tpl:
        body    = tpl.render(**ctx)
        subject = tpl.render_subject(position_title=position_title)
    else:
        raw_body = _FALLBACK_BODIES.get((message_type, channel), "")
        body     = raw_body.format(**ctx)
        raw_subj = _FALLBACK_SUBJECTS.get(message_type, "")
        subject  = raw_subj.format(**ctx)
        logger.debug(
            "No active MessageTemplate for %s/%s — using hardcoded fallback",
            message_type, channel,
        )

    return subject, body


# ─────────────────────────────────────────────────────────────────────────────
# Orchestrators
# ─────────────────────────────────────────────────────────────────────────────

def send_cv_request(application: Application, qualified: bool) -> list[Message]:
    """
    Send CV request after Claude evaluation:
      - qualified=True  → email + WhatsApp, status → awaiting_cv
      - qualified=False → WhatsApp only, status → awaiting_cv_rejected
    """
    candidate = application.candidate
    position  = application.position
    now       = timezone.now()
    created   = []

    msg_type = (
        Message.MessageType.CV_REQUEST if qualified
        else Message.MessageType.CV_REQUEST_REJECTED
    )

    # WhatsApp (always)
    _wa_subject, wa_body = _resolve_message(
        msg_type, Message.Channel.WHATSAPP,
        first_name=candidate.first_name or "",
        position_title=position.title or "",
        application_pk=application.pk,
    )
    whapi = WhapiService()
    wa_ok, wa_ext_id = whapi.send_text(candidate.phone, wa_body)
    created.append(Message.objects.create(
        application=application,
        channel=Message.Channel.WHATSAPP,
        message_type=msg_type,
        status=Message.Status.SENT if wa_ok else Message.Status.FAILED,
        external_id=wa_ext_id,
        body=wa_body,
        sent_at=now if wa_ok else None,
        error_detail=None if wa_ok else "Whapi send failed",
    ))

    # Email (qualified only)
    if qualified and candidate.email:
        email_subject, email_body = _resolve_message(
            msg_type, Message.Channel.EMAIL,
            first_name=candidate.first_name or "",
            position_title=position.title or "",
            application_pk=application.pk,
        )
        gmail = GmailService()
        email_ext_id = gmail.send_email(candidate.email, email_subject, email_body)
        created.append(Message.objects.create(
            application=application,
            channel=Message.Channel.EMAIL,
            message_type=msg_type,
            status=Message.Status.SENT if email_ext_id else Message.Status.FAILED,
            external_id=email_ext_id,
            body=email_body,
            sent_at=now if email_ext_id else None,
            error_detail=None if email_ext_id else "Gmail send failed",
        ))

    set_awaiting_cv(
        application,
        rejected=not qualified,
        note=f"CV request sent (qualified={qualified})",
    )

    logger.info(
        "CV request sent: application=%s qualified=%s messages=%s",
        application.pk, qualified, [m.pk for m in created],
    )
    return created


def send_followup(application: Application, message_type: str) -> list[Message]:
    """
    Send a follow-up message for qualified candidates (email + WhatsApp).
    """
    candidate = application.candidate
    position  = application.position
    now       = timezone.now()
    created   = []

    # WhatsApp
    _wa_subj, wa_body = _resolve_message(
        message_type, Message.Channel.WHATSAPP,
        first_name=candidate.first_name or "",
        position_title=position.title or "",
        application_pk=application.pk,
    )
    whapi = WhapiService()
    wa_ok, wa_ext_id = whapi.send_text(candidate.phone, wa_body)
    wa_msg = Message.objects.create(
        application=application,
        channel=Message.Channel.WHATSAPP,
        message_type=message_type,
        status=Message.Status.SENT if wa_ok else Message.Status.FAILED,
        external_id=wa_ext_id,
        body=wa_body,
        sent_at=now if wa_ok else None,
        error_detail=None if wa_ok else "Whapi send failed",
    )
    created.append(wa_msg)

    # Email
    if candidate.email:
        email_subject, email_body = _resolve_message(
            message_type, Message.Channel.EMAIL,
            first_name=candidate.first_name or "",
            position_title=position.title or "",
            application_pk=application.pk,
        )
        gmail = GmailService()
        email_ext_id = gmail.send_email(candidate.email, email_subject, email_body)
        email_msg = Message.objects.create(
            application=application,
            channel=Message.Channel.EMAIL,
            message_type=message_type,
            status=Message.Status.SENT if email_ext_id else Message.Status.FAILED,
            external_id=email_ext_id,
            body=email_body,
            sent_at=now if email_ext_id else None,
            error_detail=None if email_ext_id else "Gmail send failed",
        )
        created.append(email_msg)

    logger.info(
        "Follow-up sent: application=%s type=%s messages=%s",
        application.pk, message_type, [m.pk for m in created],
    )
    return created


# ─────────────────────────────────────────────────────────────────────────────
# Inbound reply persistence (shared between webhook and scheduler)
# ─────────────────────────────────────────────────────────────────────────────

def save_candidate_reply(
    *,
    sender: str,
    channel: str,
    body: str,
    subject: str = "",
    external_id: str | None = None,
) -> None:
    """
    Persist an inbound message (email or WhatsApp) as a CandidateReply.

    Resolves the sender to a Candidate and their most recent open Application.
    Both FKs are optional — an unmatched sender still produces a record.

    Args:
        sender:      Raw phone number (WhatsApp) or email address (email).
        channel:     "email" or "whatsapp" — must match CandidateReply.Channel choices.
        body:        Plain-text message body.
        subject:     Email subject line (ignored for WhatsApp).
        external_id: Message-platform-specific ID for deduplication.
    """
    from applications.models import Application as App
    from candidates.services import lookup_candidate_by_email, lookup_candidate_by_phone
    from messaging.models import CandidateReply

    _logger = logging.getLogger(__name__)

    # Resolve sender to candidate + application — failures are non-fatal
    candidate = None
    application = None
    try:
        if "@" in sender:
            candidate = lookup_candidate_by_email(sender)
        else:
            candidate = lookup_candidate_by_phone(sender)

        if candidate:
            application = (
                App.objects
                .filter(candidate=candidate)
                .exclude(status=App.Status.CLOSED)
                .order_by("-updated_at")
                .first()
            )
    except Exception as exc:
        _logger.warning(
            "Candidate/application lookup failed for sender=%s: %s", sender, exc, exc_info=True
        )

    # DB write: let this propagate so the caller (webhook) can return a 5xx for retry
    CandidateReply.objects.create(
        candidate=candidate,
        application=application,
        channel=channel,
        sender=sender,
        subject=subject,
        body=body,
        external_id=external_id,
    )
    _logger.info(
        "CandidateReply saved: channel=%s sender=%s candidate=%s application=%s",
        channel,
        sender,
        candidate.pk if candidate else None,
        application.pk if application else None,
    )
