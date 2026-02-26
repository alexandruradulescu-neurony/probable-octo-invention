from django.core.cache import cache

from applications.models import Application
from candidates.models import Candidate
from cvs.models import UnmatchedInbound, CVUpload
from messaging.models import CandidateReply
from positions.models import Position

SIDEBAR_CACHE_KEY = "sidebar_counts"
SIDEBAR_CACHE_TTL = 60


def sidebar_counts(request):
    if not request.user.is_authenticated:
        return {}

    counts = cache.get(SIDEBAR_CACHE_KEY)
    if counts is not None:
        return counts

    counts = {
        "sidebar_position_count": Position.objects.filter(
            status=Position.Status.OPEN
        ).count(),
        "sidebar_candidate_count": Candidate.objects.count(),
        "sidebar_qualified_application_count": Application.objects.filter(
            status__in=[
                Application.Status.QUALIFIED,
                Application.Status.AWAITING_CV,
                Application.Status.CV_FOLLOWUP_1,
                Application.Status.CV_FOLLOWUP_2,
                Application.Status.CV_OVERDUE,
                Application.Status.CV_RECEIVED,
            ]
        ).count(),
        "sidebar_cv_inbox_count": (
            UnmatchedInbound.objects.filter(resolved=False).count()
            + CVUpload.objects.filter(needs_review=True).count()
        ),
        "sidebar_unread_reply_count": CandidateReply.objects.filter(is_read=False).count(),
    }

    cache.set(SIDEBAR_CACHE_KEY, counts, SIDEBAR_CACHE_TTL)
    return counts
