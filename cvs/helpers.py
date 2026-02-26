from applications.models import Application
from applications.transitions import set_cv_received
from cvs.constants import AWAITING_CV_STATUSES
from cvs.models import CVUpload


def advance_application_status(app: Application) -> bool:
    """
    Advance an application to the appropriate CV-received status.
    Qualified path -> CV_RECEIVED; rejected path -> CV_RECEIVED_REJECTED.

    Returns True if the status was advanced, False if it was not applicable.
    """
    if app.status == Application.Status.AWAITING_CV_REJECTED:
        set_cv_received(app, rejected=True, note="CV received via inbox flow")
        return True
    elif app.status in AWAITING_CV_STATUSES:
        set_cv_received(app, rejected=False, note="CV received via inbox flow")
        return True
    return False


def channel_to_source(channel: str) -> str:
    """Map inbound channel string to CVUpload source."""
    return {
        "email": CVUpload.Source.EMAIL_ATTACHMENT,
        "whatsapp": CVUpload.Source.WHATSAPP_MEDIA,
    }.get((channel or "").lower(), CVUpload.Source.MANUAL_UPLOAD)
