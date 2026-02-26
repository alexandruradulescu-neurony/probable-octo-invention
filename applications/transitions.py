from django.db import transaction
from django.utils import timezone

from applications.models import Application


def _default_note(status: str) -> str:
    return f"Automatic transition to {status}"


def transition_status(
    application: Application,
    new_status: str,
    *,
    changed_by=None,
    note: str | None = None,
) -> None:
    application.change_status(
        new_status,
        changed_by=changed_by,
        note=note or _default_note(new_status),
    )


def set_call_in_progress(
    application: Application,
    *,
    changed_by=None,
    note: str | None = None,
) -> None:
    transition_status(
        application,
        Application.Status.CALL_IN_PROGRESS,
        changed_by=changed_by,
        note=note,
    )


def set_call_failed(
    application: Application,
    *,
    changed_by=None,
    note: str | None = None,
) -> None:
    transition_status(
        application,
        Application.Status.CALL_FAILED,
        changed_by=changed_by,
        note=note,
    )


def set_scoring(
    application: Application,
    *,
    changed_by=None,
    note: str | None = None,
) -> None:
    transition_status(
        application,
        Application.Status.SCORING,
        changed_by=changed_by,
        note=note,
    )


def set_qualified(
    application: Application,
    *,
    changed_by=None,
    note: str | None = None,
) -> None:
    transition_status(
        application,
        Application.Status.QUALIFIED,
        changed_by=changed_by,
        note=note,
    )


def set_not_qualified(
    application: Application,
    *,
    changed_by=None,
    note: str | None = None,
) -> None:
    transition_status(
        application,
        Application.Status.NOT_QUALIFIED,
        changed_by=changed_by,
        note=note,
    )


def set_callback_scheduled(
    application: Application,
    *,
    callback_at=None,
    changed_by=None,
    note: str | None = None,
) -> None:
    with transaction.atomic():
        if callback_at is not None:
            application.callback_scheduled_at = callback_at
            application.save(update_fields=["callback_scheduled_at", "updated_at"])
        transition_status(
            application,
            Application.Status.CALLBACK_SCHEDULED,
            changed_by=changed_by,
            note=note,
        )


def set_needs_human(
    application: Application,
    *,
    reason: str,
    changed_by=None,
    note: str | None = None,
) -> None:
    with transaction.atomic():
        application.needs_human_reason = reason
        application.save(update_fields=["needs_human_reason", "updated_at"])
        transition_status(
            application,
            Application.Status.NEEDS_HUMAN,
            changed_by=changed_by,
            note=note,
        )


def set_awaiting_cv(
    application: Application,
    *,
    rejected: bool = False,
    changed_by=None,
    note: str | None = None,
) -> None:
    transition_status(
        application,
        Application.Status.AWAITING_CV_REJECTED if rejected else Application.Status.AWAITING_CV,
        changed_by=changed_by,
        note=note,
    )


def set_cv_received(
    application: Application,
    *,
    rejected: bool = False,
    changed_by=None,
    note: str | None = None,
) -> None:
    with transaction.atomic():
        application.cv_received_at = timezone.now()
        application.save(update_fields=["cv_received_at", "updated_at"])
        transition_status(
            application,
            Application.Status.CV_RECEIVED_REJECTED if rejected else Application.Status.CV_RECEIVED,
            changed_by=changed_by,
            note=note,
        )


def set_followup_status(
    application: Application,
    new_status: str,
    *,
    changed_by=None,
    note: str | None = None,
) -> None:
    transition_status(application, new_status, changed_by=changed_by, note=note)


def set_closed(application: Application, *, changed_by=None, note: str | None = None) -> None:
    transition_status(
        application,
        Application.Status.CLOSED,
        changed_by=changed_by,
        note=note,
    )
