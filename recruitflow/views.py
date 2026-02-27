"""
recruitflow/views.py

Dashboard (Home) — high-level pipeline overview.
Spec § 12.2.
"""

from datetime import timedelta

from django.contrib.auth.mixins import LoginRequiredMixin
from django.db.models import Count, Q
from django.http import JsonResponse
from django.urls import reverse
from django.utils import timezone
from django.views import View
from django.views.generic import TemplateView

from applications.models import Application, StatusChange
from calls.models import Call
from candidates.models import Candidate
from cvs.constants import AWAITING_CV_STATUSES as _AWAITING_CV_STATUSES_CONST
from cvs.models import CVUpload, UnmatchedInbound
from messaging.models import Message
from positions.models import Position


class GlobalSearchView(LoginRequiredMixin, View):
    """
    GET /search/?q=<query>

    Returns a JSON list of matching records across Candidates, Positions,
    and Applications. Used by the topbar search dropdown.
    Minimum query length: 2 characters.
    """

    MAX_PER_TYPE = 5

    def get(self, request):
        q = (request.GET.get("q") or "").strip()
        if len(q) < 2:
            return JsonResponse({"results": []})

        results = []

        # Candidates
        for c in (
            Candidate.objects
            .filter(
                Q(first_name__icontains=q) | Q(last_name__icontains=q)
                | Q(email__icontains=q)    | Q(phone__icontains=q)
            )[:self.MAX_PER_TYPE]
        ):
            results.append({
                "type":  "Candidate",
                "label": c.full_name,
                "sub":   c.phone or c.email or "",
                "url":   reverse("candidates:detail", args=[c.pk]),
            })

        # Positions
        for p in Position.objects.filter(title__icontains=q)[:self.MAX_PER_TYPE]:
            results.append({
                "type":  "Position",
                "label": p.title,
                "sub":   p.get_status_display(),
                "url":   reverse("positions:edit", args=[p.pk]),
            })

        # Applications (match by candidate name or position title)
        for a in (
            Application.objects
            .filter(
                Q(candidate__first_name__icontains=q)
                | Q(candidate__last_name__icontains=q)
                | Q(position__title__icontains=q)
            )
            .select_related("candidate", "position")
            [:self.MAX_PER_TYPE]
        ):
            results.append({
                "type":  "Application",
                "label": f"{a.candidate.full_name} — {a.position.title}",
                "sub":   a.get_status_display(),
                "url":   reverse("applications:detail", args=[a.pk]),
            })

        return JsonResponse({"results": results, "query": q})


class DashboardView(LoginRequiredMixin, TemplateView):
    """
    Spec § 12.2 — Dashboard (Home)

    Context:
      - position_summaries : per-position status breakdown
      - activity_feed      : today's calls, CVs received, follow-ups sent
      - attention_required : items needing recruiter action
      - pipeline_data      : aggregated status counts across all applications
      - recent_changes     : latest StatusChange audit trail entries
    """
    template_name = "dashboard.html"

    PENDING_STATUSES = {
        Application.Status.PENDING_CALL,
        Application.Status.CALL_QUEUED,
    }
    IN_PROGRESS_STATUSES = {
        Application.Status.CALL_IN_PROGRESS,
        Application.Status.CALL_COMPLETED,
        Application.Status.SCORING,
        Application.Status.QUALIFIED,
        Application.Status.NOT_QUALIFIED,
        Application.Status.CALLBACK_SCHEDULED,
        Application.Status.NEEDS_HUMAN,
        Application.Status.CALL_FAILED,
    }
    AWAITING_CV_STATUSES = _AWAITING_CV_STATUSES_CONST
    COMPLETED_STATUSES = {
        Application.Status.CV_RECEIVED,
        Application.Status.CV_RECEIVED_REJECTED,
        Application.Status.CLOSED,
    }

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        now = timezone.now()
        today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)

        ctx["position_summaries"] = self._position_summaries()
        ctx["activity_feed"] = self._activity_feed(today_start)
        ctx["attention_required"] = self._attention_required(now, today_start)
        ctx["pipeline_data"] = self._pipeline_data()
        ctx["recent_changes"] = self._recent_changes()

        return ctx

    # ── Position summary cards ─────────────────────────────────────────────────

    @classmethod
    def _position_summaries(cls) -> list[dict]:
        """
        Per-position candidate breakdown by status group.
        Groups: pending calls, in progress, awaiting CV, completed/closed.
        """
        pending_statuses = cls.PENDING_STATUSES
        in_progress_statuses = cls.IN_PROGRESS_STATUSES
        awaiting_cv_statuses = cls.AWAITING_CV_STATUSES
        completed_statuses = cls.COMPLETED_STATUSES

        positions = (
            Position.objects
            .filter(status=Position.Status.OPEN)
            .annotate(
                pending_calls=Count(
                    "applications",
                    filter=Q(applications__status__in=pending_statuses),
                ),
                in_progress=Count(
                    "applications",
                    filter=Q(applications__status__in=in_progress_statuses),
                ),
                awaiting_cv=Count(
                    "applications",
                    filter=Q(applications__status__in=awaiting_cv_statuses),
                ),
                completed=Count(
                    "applications",
                    filter=Q(applications__status__in=completed_statuses),
                ),
                total=Count("applications"),
            )
        )

        return [
            {
                "position": pos,
                "pending_calls": pos.pending_calls,
                "in_progress": pos.in_progress,
                "awaiting_cv": pos.awaiting_cv,
                "completed": pos.completed,
                "total": pos.total,
            }
            for pos in positions[:15]
        ]

    # ── Activity feed ──────────────────────────────────────────────────────────

    @staticmethod
    def _activity_feed(today_start) -> dict:
        return {
            "calls_today": Call.objects.filter(
                initiated_at__gte=today_start
            ).count(),
            "cvs_today": CVUpload.objects.filter(
                received_at__gte=today_start
            ).count(),
            "followups_today": Message.objects.filter(
                sent_at__gte=today_start,
                message_type__in=[
                    Message.MessageType.CV_FOLLOWUP_1,
                    Message.MessageType.CV_FOLLOWUP_2,
                ],
            ).count(),
        }

    # ── Pipeline data ──────────────────────────────────────────────────────────

    @classmethod
    def _pipeline_data(cls) -> list[dict]:
        """
        Aggregate application counts by pipeline stage for the bar chart.
        Maps the app's actual status choices into meaningful pipeline stages.
        """
        pending_statuses = cls.PENDING_STATUSES
        screening_statuses = {
            Application.Status.CALL_IN_PROGRESS,
            Application.Status.CALL_COMPLETED,
            Application.Status.SCORING,
            Application.Status.CALLBACK_SCHEDULED,
            Application.Status.CALL_FAILED,
            Application.Status.NEEDS_HUMAN,
        }
        qualified_statuses = {
            Application.Status.QUALIFIED,
            Application.Status.NOT_QUALIFIED,
        }
        awaiting_cv_statuses = cls.AWAITING_CV_STATUSES
        completed_statuses = cls.COMPLETED_STATUSES

        # Single aggregate query replaces 5 separate COUNT queries.
        totals = Application.objects.aggregate(
            pending=Count("id", filter=Q(status__in=pending_statuses)),
            screening=Count("id", filter=Q(status__in=screening_statuses)),
            evaluated=Count("id", filter=Q(status__in=qualified_statuses)),
            awaiting_cv=Count("id", filter=Q(status__in=awaiting_cv_statuses)),
            completed=Count("id", filter=Q(status__in=completed_statuses)),
        )

        stages = [
            {"label": "Pending",     "count": totals["pending"],     "color": "#94A3B8"},
            {"label": "Screening",   "count": totals["screening"],   "color": "#6366F1"},
            {"label": "Evaluated",   "count": totals["evaluated"],   "color": "#4F46E5"},
            {"label": "Awaiting CV", "count": totals["awaiting_cv"], "color": "#F59E0B"},
            {"label": "Completed",   "count": totals["completed"],   "color": "#10B981"},
        ]

        max_count = max((s["count"] for s in stages), default=1) or 1
        for s in stages:
            s["pct"] = round(s["count"] / max_count * 100)

        return stages

    # ── Recent changes (activity feed) ─────────────────────────────────────────

    @staticmethod
    def _recent_changes() -> list:
        """
        Last 10 status changes across all applications, used for the
        activity feed widget on the dashboard.
        """
        return (
            StatusChange.objects
            .select_related(
                "application__candidate",
                "application__position",
                "changed_by",
            )
            .order_by("-changed_at")[:10]
        )

    # ── Attention required ─────────────────────────────────────────────────────

    @staticmethod
    def _attention_required(now, today_start) -> dict:
        """
        Items that require recruiter action.

        The four Application-based counts are collapsed into a single aggregate query.
        UnmatchedInbound and CVUpload counts remain as separate queries (different models).
        """
        today_end = today_start + timedelta(days=1)

        # Single aggregate replaces 4 separate Application COUNT queries.
        app_totals = Application.objects.aggregate(
            call_failures=Count(
                "id",
                filter=Q(status=Application.Status.CALL_FAILED),
            ),
            cv_overdue=Count(
                "id",
                filter=Q(status=Application.Status.CV_OVERDUE),
            ),
            needs_human=Count(
                "id",
                filter=Q(status=Application.Status.NEEDS_HUMAN),
            ),
            callbacks_today=Count(
                "id",
                filter=Q(
                    status=Application.Status.CALLBACK_SCHEDULED,
                    callback_scheduled_at__gte=today_start,
                    callback_scheduled_at__lt=today_end,
                ),
            ),
        )

        return {
            "call_failures":    app_totals["call_failures"],
            "cv_overdue":       app_totals["cv_overdue"],
            "needs_human":      app_totals["needs_human"],
            "callbacks_today":  app_totals["callbacks_today"],
            "unmatched_inbound": UnmatchedInbound.objects.filter(resolved=False).count(),
            "needs_review_cvs":  CVUpload.objects.filter(needs_review=True).count(),
        }
