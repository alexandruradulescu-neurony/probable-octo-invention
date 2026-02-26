"""
recruitflow/views.py

Dashboard (Home) — high-level pipeline overview.
Spec § 12.2.
"""

from datetime import timedelta

from django.contrib.auth.mixins import LoginRequiredMixin
from django.db.models import Count, Q
from django.utils import timezone
from django.views.generic import TemplateView

from applications.models import Application, StatusChange
from calls.models import Call
from candidates.models import Candidate
from cvs.models import CVUpload, UnmatchedInbound
from messaging.models import Message
from positions.models import Position


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

    @staticmethod
    def _position_summaries() -> list[dict]:
        """
        Per-position candidate breakdown by status group.
        Groups: pending calls, in progress, awaiting CV, completed/closed.
        """
        positions = Position.objects.filter(status=Position.Status.OPEN)

        pending_statuses = {
            Application.Status.PENDING_CALL,
            Application.Status.CALL_QUEUED,
        }
        in_progress_statuses = {
            Application.Status.CALL_IN_PROGRESS,
            Application.Status.CALL_COMPLETED,
            Application.Status.SCORING,
            Application.Status.QUALIFIED,
            Application.Status.NOT_QUALIFIED,
            Application.Status.CALLBACK_SCHEDULED,
            Application.Status.NEEDS_HUMAN,
            Application.Status.CALL_FAILED,
        }
        awaiting_cv_statuses = {
            Application.Status.AWAITING_CV,
            Application.Status.CV_FOLLOWUP_1,
            Application.Status.CV_FOLLOWUP_2,
            Application.Status.CV_OVERDUE,
            Application.Status.AWAITING_CV_REJECTED,
        }
        completed_statuses = {
            Application.Status.CV_RECEIVED,
            Application.Status.CV_RECEIVED_REJECTED,
            Application.Status.CLOSED,
        }

        summaries = []
        for pos in positions:
            apps = Application.objects.filter(position=pos)
            summaries.append({
                "position": pos,
                "pending_calls": apps.filter(status__in=pending_statuses).count(),
                "in_progress": apps.filter(status__in=in_progress_statuses).count(),
                "awaiting_cv": apps.filter(status__in=awaiting_cv_statuses).count(),
                "completed": apps.filter(status__in=completed_statuses).count(),
                "total": apps.count(),
            })
        return summaries

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

    @staticmethod
    def _pipeline_data() -> list[dict]:
        """
        Aggregate application counts by pipeline stage for the bar chart.
        Maps the app's actual status choices into meaningful pipeline stages.
        """
        pending_statuses = {
            Application.Status.PENDING_CALL,
            Application.Status.CALL_QUEUED,
        }
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
        awaiting_cv_statuses = {
            Application.Status.AWAITING_CV,
            Application.Status.CV_FOLLOWUP_1,
            Application.Status.CV_FOLLOWUP_2,
            Application.Status.CV_OVERDUE,
            Application.Status.AWAITING_CV_REJECTED,
        }
        completed_statuses = {
            Application.Status.CV_RECEIVED,
            Application.Status.CV_RECEIVED_REJECTED,
            Application.Status.CLOSED,
        }

        stages = [
            {"label": "Pending",     "count": Application.objects.filter(status__in=pending_statuses).count(),     "color": "#A3AED0"},
            {"label": "Screening",   "count": Application.objects.filter(status__in=screening_statuses).count(),   "color": "#7551FF"},
            {"label": "Evaluated",   "count": Application.objects.filter(status__in=qualified_statuses).count(),   "color": "#4318FF"},
            {"label": "Awaiting CV", "count": Application.objects.filter(status__in=awaiting_cv_statuses).count(), "color": "#F0B429"},
            {"label": "Completed",   "count": Application.objects.filter(status__in=completed_statuses).count(),   "color": "#01B574"},
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
        """
        today_end = today_start + timedelta(days=1)

        return {
            "call_failures": Application.objects.filter(
                status=Application.Status.CALL_FAILED,
            ).count(),
            "cv_overdue": Application.objects.filter(
                status=Application.Status.CV_OVERDUE,
            ).count(),
            "needs_human": Application.objects.filter(
                status=Application.Status.NEEDS_HUMAN,
            ).count(),
            "callbacks_today": Application.objects.filter(
                status=Application.Status.CALLBACK_SCHEDULED,
                callback_scheduled_at__gte=today_start,
                callback_scheduled_at__lt=today_end,
            ).count(),
            "unmatched_inbound": UnmatchedInbound.objects.filter(
                resolved=False,
            ).count(),
            "needs_review_cvs": CVUpload.objects.filter(
                needs_review=True,
            ).count(),
        }
