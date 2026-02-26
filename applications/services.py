import logging
import uuid

from django.core.files.storage import default_storage
from django.db import transaction

from applications.models import Application
from applications.transitions import set_cv_received
from cvs.constants import AWAITING_CV_STATUSES
from cvs.models import CVUpload

logger = logging.getLogger(__name__)


def handle_manual_cv_upload(application: Application, uploaded_file, changed_by=None) -> CVUpload:
    """
    Persist a manually uploaded CV and advance status on ALL of the candidate's
    awaiting-CV applications (mirrors the auto-matching pipeline behaviour).

    The candidate FK is set on every CVUpload so the CV appears on the
    candidate profile page directly.

    Status logic: uses the application's status to decide the rejected path —
    not the qualified boolean — so a pending/unscored candidate isn't
    incorrectly routed to CV_RECEIVED_REJECTED.
    """
    candidate = application.candidate

    unique_name = f"cvs/{uuid.uuid4().hex[:8]}_{uploaded_file.name}"
    saved_path = default_storage.save(unique_name, uploaded_file)

    # Find ALL awaiting-CV applications for this candidate so the file is
    # attached to every open application, not just the one the user clicked on.
    awaiting_apps = list(
        Application.objects.filter(
            candidate=candidate,
            status__in=list(AWAITING_CV_STATUSES),
        )
    )
    # Always include the anchor application even if its status is outside the set
    # (e.g. a recruiter uploads mid-pipeline) — it will still get the CVUpload record.
    target_apps = awaiting_apps if awaiting_apps else [application]

    first_upload = None
    with transaction.atomic():
        for app in target_apps:
            cv = CVUpload.objects.create(
                candidate=candidate,
                application=app,
                file_name=uploaded_file.name,
                file_path=saved_path,
                source=CVUpload.Source.MANUAL_UPLOAD,
                match_method=CVUpload.MatchMethod.MANUAL,
            )
            if first_upload is None:
                first_upload = cv

            if app.status in AWAITING_CV_STATUSES:
                # Derive rejected path from the application's own status, not
                # the qualified boolean (which may be None before evaluation).
                rejected = app.status == Application.Status.AWAITING_CV_REJECTED
                set_cv_received(
                    app,
                    rejected=rejected,
                    changed_by=changed_by,
                    note="CV manually uploaded",
                )

    logger.info(
        "Manual CV upload: candidate=%s file=%s apps_updated=%s",
        candidate.pk, uploaded_file.name, [a.pk for a in target_apps],
    )
    return first_upload
