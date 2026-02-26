from applications.models import Application

# Application statuses where a CV submission is expected.
AWAITING_CV_STATUSES = frozenset({
    Application.Status.AWAITING_CV,
    Application.Status.CV_FOLLOWUP_1,
    Application.Status.CV_FOLLOWUP_2,
    Application.Status.CV_OVERDUE,
    Application.Status.AWAITING_CV_REJECTED,
})
