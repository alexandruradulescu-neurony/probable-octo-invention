import uuid

from django.core.files.storage import default_storage
from django.utils import timezone

from applications.models import Application
from cvs.models import CVUpload

_CV_AWAITING_STATUSES = frozenset({
    Application.Status.AWAITING_CV,
    Application.Status.CV_FOLLOWUP_1,
    Application.Status.CV_FOLLOWUP_2,
    Application.Status.CV_OVERDUE,
    Application.Status.AWAITING_CV_REJECTED,
})


def handle_manual_cv_upload(application: Application, uploaded_file, changed_by=None) -> CVUpload:
    """
    Persist a manually uploaded CV and advance application status when applicable.
    """
    unique_name = f"cvs/{uuid.uuid4().hex[:8]}_{uploaded_file.name}"
    saved_path = default_storage.save(unique_name, uploaded_file)

    cv_upload = CVUpload.objects.create(
        application=application,
        file_name=uploaded_file.name,
        file_path=saved_path,
        source=CVUpload.Source.MANUAL_UPLOAD,
        match_method=CVUpload.MatchMethod.MANUAL,
    )

    if application.status in _CV_AWAITING_STATUSES:
        new_status = (
            Application.Status.CV_RECEIVED
            if application.qualified
            else Application.Status.CV_RECEIVED_REJECTED
        )
        application.cv_received_at = timezone.now()
        application.save(update_fields=["cv_received_at", "updated_at"])
        application.change_status(
            new_status,
            changed_by=changed_by,
            note="CV manually uploaded",
        )

    return cv_upload
