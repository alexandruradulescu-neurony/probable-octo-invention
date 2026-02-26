from applications.models import Application
from candidates.models import Candidate
from cvs.models import UnmatchedInbound, CVUpload
from positions.models import Position


def sidebar_counts(request):
    if not request.user.is_authenticated:
        return {}

    return {
        "sidebar_position_count": Position.objects.filter(
            status=Position.Status.OPEN
        ).count(),
        "sidebar_candidate_count": Candidate.objects.count(),
        "sidebar_application_count": Application.objects.exclude(
            status=Application.Status.CLOSED
        ).count(),
        "sidebar_cv_inbox_count": (
            UnmatchedInbound.objects.filter(resolved=False).count()
            + CVUpload.objects.filter(needs_review=True).count()
        ),
    }
