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
from messaging.models import Message

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# WhapiService
# ─────────────────────────────────────────────────────────────────────────────

class WhapiService:
    """Send WhatsApp messages via the Whapi REST API."""

    def __init__(self):
        self.token = settings.WHAPI_TOKEN
        self.base_url = (settings.WHAPI_API_URL or "").rstrip("/")

    def send_text(self, phone: str, body: str) -> str | None:
        """
        Send a plain-text WhatsApp message.

        Args:
            phone: Recipient phone in E.164 digits (e.g. "40712345678").
            body:  Message text.

        Returns:
            External message ID on success, or None on failure.
        """
        if not self.token or not self.base_url:
            logger.warning("Whapi credentials not configured — message not sent")
            return None

        url = f"{self.base_url}/messages/text"
        payload = {
            "to": f"{phone}@s.whatsapp.net",
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
            msg_id = data.get("message_id") or data.get("id")
            logger.info("WhatsApp sent to %s: external_id=%s", phone, msg_id)
            return msg_id
        except http_requests.RequestException as exc:
            logger.error("Whapi send failed to %s: %s", phone, exc)
            return None


# ─────────────────────────────────────────────────────────────────────────────
# GmailService
# ─────────────────────────────────────────────────────────────────────────────

class GmailService:
    """
    Send emails and poll inbox via Gmail API using OAuth2 refresh tokens.

    Requires google-api-python-client + google-auth-oauthlib.
    """

    _service = None

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
        refresh_token = settings.GOOGLE_REFRESH_TOKEN

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

        try:
            result = svc.users().messages().send(
                userId="me",
                body={"raw": raw},
            ).execute()
            msg_id = result.get("id")
            logger.info("Gmail sent to %s: id=%s", to, msg_id)
            return msg_id
        except Exception as exc:
            logger.error("Gmail send failed to %s: %s", to, exc)
            return None

    def list_unread_with_attachments(self, label: str) -> list[dict]:
        """
        List unread messages in the given label that have attachments.

        Returns list of dicts: {id, sender, subject, body_snippet, attachments: [{name, data}]}
        """
        try:
            svc = self.service
        except Exception as exc:
            logger.error("Gmail service init failed: %s", exc)
            return []

        try:
            query = f"label:{label} is:unread has:attachment"
            result = svc.users().messages().list(
                userId="me", q=query, maxResults=20
            ).execute()
            message_ids = [m["id"] for m in result.get("messages", [])]
        except Exception as exc:
            logger.error("Gmail list failed: %s", exc)
            return []

        messages = []
        for mid in message_ids:
            try:
                msg_data = self._fetch_message_with_attachments(svc, mid)
                if msg_data:
                    messages.append(msg_data)
            except Exception as exc:
                logger.warning("Failed to fetch Gmail message %s: %s", mid, exc)

        return messages

    @staticmethod
    def _fetch_message_with_attachments(svc, message_id: str) -> dict | None:
        """Fetch a single Gmail message and download its attachments."""
        msg = svc.users().messages().get(
            userId="me", id=message_id, format="full"
        ).execute()

        headers = {h["name"].lower(): h["value"] for h in msg.get("payload", {}).get("headers", [])}
        sender = headers.get("from", "")
        subject = headers.get("subject", "")
        snippet = msg.get("snippet", "")

        attachments = []
        parts = msg.get("payload", {}).get("parts", [])
        for part in parts:
            filename = part.get("filename")
            body = part.get("body", {})
            att_id = body.get("attachmentId")
            if filename and att_id:
                att = svc.users().messages().attachments().get(
                    userId="me", messageId=message_id, id=att_id
                ).execute()
                data = base64.urlsafe_b64decode(att["data"])
                attachments.append({"name": filename, "data": data})

        if not attachments:
            return None

        return {
            "id": message_id,
            "sender": sender,
            "subject": subject,
            "body_snippet": snippet,
            "attachments": attachments,
        }

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
# Message body templates
# ─────────────────────────────────────────────────────────────────────────────

def _cv_request_body(candidate, position, qualified: bool) -> str:
    if qualified:
        return (
            f"Hi {candidate.first_name},\n\n"
            f"Great news! Following your recent call about the {position.title} position, "
            f"we'd like to move forward with your application.\n\n"
            f"Could you please send us your CV/resume at your earliest convenience?\n\n"
            f"Your application reference is #{candidate.applications.filter(position=position).first().pk}.\n\n"
            f"Thank you!\nThe {position.title} Recruitment Team"
        )
    return (
        f"Hi {candidate.first_name},\n\n"
        f"Thank you for your interest in the {position.title} position and for taking "
        f"the time to speak with us.\n\n"
        f"While this particular role may not be the best fit right now, we'd love to "
        f"keep your details on file for future opportunities. If you'd like, please "
        f"send us your CV/resume.\n\n"
        f"Best regards,\nThe Recruitment Team"
    )


def _followup_body(candidate, position, message_type: str) -> str:
    if message_type == Message.MessageType.CV_FOLLOWUP_1:
        return (
            f"Hi {candidate.first_name}, just a gentle reminder — "
            f"we're still waiting for your CV for the {position.title} role. "
            f"Please send it at your earliest convenience."
        )
    return (
        f"Hi {candidate.first_name}, this is a final reminder regarding "
        f"your CV for the {position.title} position. "
        f"Please send it as soon as possible so we can continue with your application."
    )


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
    position = application.position
    body = _cv_request_body(candidate, position, qualified)
    now = timezone.now()
    created = []

    if qualified:
        msg_type = Message.MessageType.CV_REQUEST
        new_status = Application.Status.AWAITING_CV
    else:
        msg_type = Message.MessageType.CV_REQUEST_REJECTED
        new_status = Application.Status.AWAITING_CV_REJECTED

    # WhatsApp (always)
    whapi = WhapiService()
    wa_ext_id = whapi.send_text(candidate.phone, body)
    wa_msg = Message.objects.create(
        application=application,
        channel=Message.Channel.WHATSAPP,
        message_type=msg_type,
        status=Message.Status.SENT if wa_ext_id else Message.Status.FAILED,
        external_id=wa_ext_id,
        body=body,
        sent_at=now if wa_ext_id else None,
        error_detail=None if wa_ext_id else "Whapi send failed",
    )
    created.append(wa_msg)

    # Email (qualified only)
    if qualified and candidate.email:
        gmail = GmailService()
        subject = f"CV Request — {position.title}"
        email_ext_id = gmail.send_email(candidate.email, subject, body)
        email_msg = Message.objects.create(
            application=application,
            channel=Message.Channel.EMAIL,
            message_type=msg_type,
            status=Message.Status.SENT if email_ext_id else Message.Status.FAILED,
            external_id=email_ext_id,
            body=body,
            sent_at=now if email_ext_id else None,
            error_detail=None if email_ext_id else "Gmail send failed",
        )
        created.append(email_msg)

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
    position = application.position
    body = _followup_body(candidate, position, message_type)
    now = timezone.now()
    created = []

    # WhatsApp
    whapi = WhapiService()
    wa_ext_id = whapi.send_text(candidate.phone, body)
    wa_msg = Message.objects.create(
        application=application,
        channel=Message.Channel.WHATSAPP,
        message_type=message_type,
        status=Message.Status.SENT if wa_ext_id else Message.Status.FAILED,
        external_id=wa_ext_id,
        body=body,
        sent_at=now if wa_ext_id else None,
        error_detail=None if wa_ext_id else "Whapi send failed",
    )
    created.append(wa_msg)

    # Email
    if candidate.email:
        gmail = GmailService()
        subject = f"Reminder: CV for {position.title}"
        email_ext_id = gmail.send_email(candidate.email, subject, body)
        email_msg = Message.objects.create(
            application=application,
            channel=Message.Channel.EMAIL,
            message_type=message_type,
            status=Message.Status.SENT if email_ext_id else Message.Status.FAILED,
            external_id=email_ext_id,
            body=body,
            sent_at=now if email_ext_id else None,
            error_detail=None if email_ext_id else "Gmail send failed",
        )
        created.append(email_msg)

    logger.info(
        "Follow-up sent: application=%s type=%s messages=%s",
        application.pk, message_type, [m.pk for m in created],
    )
    return created
