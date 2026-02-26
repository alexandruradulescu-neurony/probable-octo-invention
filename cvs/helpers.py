from django.utils import timezone

from applications.models import Application
from cvs.constants import AWAITING_CV_STATUSES
from cvs.models import CVUpload


def advance_application_status(app: Application, now=None) -> None:
    """
    Advance an application to the appropriate CV-received status.
    Qualified path -> CV_RECEIVED; rejected path -> CV_RECEIVED_REJECTED.
    """
    now = now or timezone.now()

    if app.status == Application.Status.AWAITING_CV_REJECTED:
        new_status = Application.Status.CV_RECEIVED_REJECTED
    elif app.status in AWAITING_CV_STATUSES:
        new_status = Application.Status.CV_RECEIVED
    else:
        return

    app.status = new_status
    app.cv_received_at = now
    app.save(update_fields=["status", "cv_received_at", "updated_at"])


def channel_to_source(channel: str) -> str:
    """Map inbound channel string to CVUpload source."""
    return {
        "email": CVUpload.Source.EMAIL_ATTACHMENT,
        "whatsapp": CVUpload.Source.WHATSAPP_MEDIA,
    }.get((channel or "").lower(), CVUpload.Source.MANUAL_UPLOAD)
