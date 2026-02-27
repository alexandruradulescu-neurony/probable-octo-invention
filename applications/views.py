"""
applications/views.py

Application List (main daily-use screen) and Application Detail (most important screen).
Spec § 12.5.
"""

import logging

from django.contrib import messages as django_messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.cache import cache
from django.db import IntegrityError, transaction as db_transaction
from django.db.models import Prefetch
from django.shortcuts import get_object_or_404, redirect
from django.utils import timezone
from django.views import View
from django.views.generic import DetailView, ListView

from applications.forms import (
    AddNoteForm,
    ManualCVUploadForm,
    ScheduleCallbackForm,
    StatusOverrideForm,
)
from applications.models import Application, StatusChange
from applications.services import handle_manual_cv_upload
from applications.transitions import set_callback_scheduled
from calls.models import Call
from calls.services import ElevenLabsError, ElevenLabsService
from cvs.models import CVUpload
from evaluations.models import LLMEvaluation
from messaging.models import CandidateReply, Message
from positions.models import Position
from recruitflow.constants import SIDEBAR_CACHE_KEY
from recruitflow.text_utils import humanize_form_question

logger = logging.getLogger(__name__)


class ApplicationListView(LoginRequiredMixin, ListView):
    """
    Filterable application list.
    Query params: position, status, qualified, date_from, date_to.
    Spec § 12.5 — Application List.
    """
    model = Application
    template_name = "applications/application_list.html"
    context_object_name = "applications"
    paginate_by = 50

    def get_queryset(self):
        qs = (
            Application.objects
            .select_related("candidate", "position")
            .order_by("-updated_at")
        )

        position_id = self.request.GET.get("position")
        if position_id:
            qs = qs.filter(position_id=position_id)

        status = self.request.GET.get("status")
        if status:
            qs = qs.filter(status=status)

        qualified = self.request.GET.get("qualified")
        if qualified == "true":
            qs = qs.filter(qualified=True)
        elif qualified == "false":
            qs = qs.filter(qualified=False)

        date_from = self.request.GET.get("date_from")
        if date_from:
            qs = qs.filter(created_at__date__gte=date_from)

        date_to = self.request.GET.get("date_to")
        if date_to:
            qs = qs.filter(created_at__date__lte=date_to)

        return qs

    def get_context_data(self, **kwargs):
        from recruitflow.views import DashboardView

        ctx = super().get_context_data(**kwargs)
        now = timezone.now()
        today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)

        try:
            period = int(self.request.GET.get("period", 7))
        except (ValueError, TypeError):
            period = 7
        if period not in {7, 14, 30}:
            period = 7

        # Build period-tab URLs preserving all existing filter params
        def _period_url(p):
            params = self.request.GET.copy()
            params["period"] = p
            return "?" + params.urlencode()

        ctx["positions"] = Position.objects.order_by("title")
        ctx["status_choices"] = Application.Status.choices
        ctx["current_filters"] = {
            "position": self.request.GET.get("position", ""),
            "status": self.request.GET.get("status", ""),
            "qualified": self.request.GET.get("qualified", ""),
            "date_from": self.request.GET.get("date_from", ""),
            "date_to": self.request.GET.get("date_to", ""),
        }
        from datetime import timedelta
        from django.db.models import Count

        # Date range
        today = now.date()
        start_date = today - timedelta(days=period - 1)
        dates = [start_date + timedelta(days=i) for i in range(period)]
        date_labels = [f"{d.day} {d.strftime('%b')}" for d in dates]

        # Open positions ordered by title
        open_positions = list(
            Position.objects.filter(status=Position.Status.OPEN).order_by("title")
        )

        # Single query: daily application counts per open position
        rows = (
            Application.objects
            .filter(
                created_at__date__gte=start_date,
                position__in=open_positions,
            )
            .values("created_at__date", "position_id")
            .annotate(count=Count("id"))
        )
        # Build lookup: (position_id, date) → count
        counts = {(r["position_id"], r["created_at__date"]): r["count"] for r in rows}

        # Palette — cycle through distinct colours per position
        palette = [
            "rgba(79,70,229,0.80)",   # indigo
            "rgba(16,185,129,0.80)",  # green
            "rgba(245,158,11,0.80)",  # amber
            "rgba(239,68,68,0.80)",   # red
            "rgba(139,92,246,0.80)",  # violet
            "rgba(20,184,166,0.80)",  # teal
            "rgba(249,115,22,0.80)",  # orange
            "rgba(236,72,153,0.80)",  # pink
        ]

        datasets = []
        for idx, pos in enumerate(open_positions):
            color = palette[idx % len(palette)]
            datasets.append({
                "label": pos.title,
                "data":  [counts.get((pos.pk, d), 0) for d in dates],
                "backgroundColor": color,
                "borderRadius": 3,
                "borderSkipped": False,
                "stack": "daily",
            })

        ctx["period"] = period
        ctx["period_urls"] = {7: _period_url(7), 14: _period_url(14), 30: _period_url(30)}
        ctx["kpi_totals"] = DashboardView._kpi_totals(period, now, today_start)
        ctx["positions_chart_data"] = {"labels": date_labels, "datasets": datasets}
        ctx["open_positions_count"] = len(open_positions)
        return ctx


class ApplicationDetailView(LoginRequiredMixin, DetailView):
    """
    Full timeline of a single application.
    Pulls Candidate, Position, Call history, Messages, LLMEvaluations, CV uploads.
    Spec § 12.5 — Application Detail.
    """
    model = Application
    template_name = "applications/application_detail.html"
    context_object_name = "application"

    def get_queryset(self):
        return (
            Application.objects
            .select_related("candidate", "position")
            .prefetch_related(
                Prefetch(
                    "calls",
                    queryset=Call.objects.order_by("attempt_number").prefetch_related(
                        Prefetch(
                            "evaluations",
                            queryset=LLMEvaluation.objects.order_by("evaluated_at"),
                        )
                    ),
                ),
                Prefetch(
                    "messages",
                    queryset=Message.objects.order_by("-sent_at", "-id"),
                ),
                Prefetch(
                    "cv_uploads",
                    queryset=CVUpload.objects.order_by("-received_at"),
                ),
                Prefetch(
                    "status_changes",
                    queryset=StatusChange.objects.select_related("changed_by").order_by("-changed_at"),
                ),
                Prefetch(
                    "candidate_replies",
                    queryset=CandidateReply.objects.order_by("-received_at"),
                ),
            )
        )

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        app = self.object
        ctx["candidate"] = app.candidate
        ctx["position"] = app.position
        ctx["calls"] = app.calls.all()
        ctx["sent_messages"] = app.messages.all()
        ctx["candidate_replies"] = app.candidate_replies.all()
        ctx["cv_uploads"] = app.cv_uploads.all()
        ctx["status_changes"] = app.status_changes.all()

        # Action forms
        ctx["status_override_form"] = StatusOverrideForm(initial={"new_status": app.status})
        ctx["add_note_form"] = AddNoteForm()
        ctx["schedule_callback_form"] = ScheduleCallbackForm()
        ctx["manual_cv_upload_form"] = ManualCVUploadForm()

        # Format form_answers for template display
        form_answers = app.candidate.form_answers
        if form_answers and isinstance(form_answers, dict):
            ctx["form_answers_list"] = [
                {
                    "question": humanize_form_question(key),
                    "answer": value,
                }
                for key, value in form_answers.items()
            ]
        else:
            ctx["form_answers_list"] = []

        return ctx


def _queue_applications_for_calling(pks: list, user, note: str = "Queued for calling") -> tuple[int, int]:
    """
    Transition all PENDING_CALL applications in `pks` to CALL_QUEUED.

    Returns:
        (queued, skipped) counts.
    """
    apps = list(
        Application.objects.filter(
            pk__in=pks,
            status=Application.Status.PENDING_CALL,
        )
    )
    for app in apps:
        app.change_status(
            Application.Status.CALL_QUEUED,
            changed_by=user,
            note=note,
        )
    queued = len(apps)
    skipped = len(pks) - queued
    return queued, skipped


class TriggerCallsView(LoginRequiredMixin, View):
    """
    POST /applications/trigger-calls/

    Bulk action: receive a list of application PKs, validate they are in
    pending_call status, and transition them to call_queued so the scheduler
    picks them up.
    """

    def post(self, request):
        pks = request.POST.getlist("application_ids")
        if not pks:
            django_messages.warning(request, "No applications selected.")
            return redirect("applications:list")

        queued, skipped = _queue_applications_for_calling(
            pks, request.user, note="Bulk trigger: queued for calling"
        )
        if queued:
            django_messages.success(request, f"{queued} application(s) queued for calling.")
        if skipped:
            django_messages.warning(
                request,
                f"{skipped} application(s) skipped (not in Pending Call status).",
            )
        return redirect("applications:list")


class BulkActionApplicationsView(LoginRequiredMixin, View):
    """
    POST /applications/bulk-action/

    Handles three bulk actions on a selected set of applications:
      action=delete        — permanently delete selected applications
      action=move          — reassign selected applications to target_position
      action=trigger_calls — queue pending_call applications for calling
    """

    def post(self, request):
        pks    = request.POST.getlist("application_ids")
        action = request.POST.get("action", "")

        if not pks:
            django_messages.warning(request, "No applications selected.")
            return redirect("applications:list")

        qs    = Application.objects.filter(pk__in=pks)
        count = qs.count()

        if action == "delete":
            confirm = request.POST.get("confirm_delete", "").strip().lower()
            if confirm != "yes":
                django_messages.error(
                    request,
                    "Delete not confirmed. Please check the confirmation checkbox before deleting.",
                )
                return redirect("applications:list")
            qs.delete()
            cache.delete(SIDEBAR_CACHE_KEY)
            django_messages.success(request, f"Deleted {count} application(s).")

        elif action == "move":
            position_id = request.POST.get("target_position", "").strip()
            if not position_id:
                django_messages.error(request, "Select a target position before moving.")
            else:
                try:
                    pos = Position.objects.get(pk=position_id)
                    moved = 0
                    conflict = 0
                    with db_transaction.atomic():
                        for app in qs.select_related("position"):
                            try:
                                with db_transaction.atomic():  # savepoint per app
                                    old_title = app.position.title
                                    app.position = pos
                                    app.save(update_fields=["position", "updated_at"])
                                    StatusChange.objects.create(
                                        application=app,
                                        from_status=app.status,
                                        to_status=app.status,
                                        changed_by=request.user,
                                        note=f"Application moved from position '{old_title}' to '{pos.title}'",
                                    )
                                    moved += 1
                            except IntegrityError:
                                conflict += 1
                    if moved:
                        django_messages.success(
                            request,
                            f"Moved {moved} application(s) to '{pos.title}'.",
                        )
                    if conflict:
                        django_messages.warning(
                            request,
                            f"{conflict} application(s) skipped — candidate already has an application for '{pos.title}'.",
                        )
                except Position.DoesNotExist:
                    django_messages.error(request, "Selected position not found.")

        elif action == "trigger_calls":
            queued, skipped = _queue_applications_for_calling(
                pks, request.user, note="Bulk action: queued for calling"
            )
            if queued:
                django_messages.success(request, f"{queued} application(s) queued for calling.")
            if skipped:
                django_messages.warning(
                    request,
                    f"{skipped} application(s) skipped — only Pending Call status can be queued.",
                )

        else:
            django_messages.error(request, "Unknown bulk action.")

        return redirect("applications:list")


class StatusOverrideView(LoginRequiredMixin, View):
    """POST /applications/<pk>/override-status/"""

    def post(self, request, pk):
        app = get_object_or_404(Application, pk=pk)
        form = StatusOverrideForm(request.POST)
        if form.is_valid():
            new_status = form.cleaned_data["new_status"]
            reason = form.cleaned_data["reason"] or "Manual status override"
            app.change_status(new_status, changed_by=request.user, note=reason)
            django_messages.success(request, f"Status changed to {app.get_status_display()}.")
        else:
            django_messages.error(request, "Invalid status override.")
        return redirect("applications:detail", pk=pk)


class AddNoteView(LoginRequiredMixin, View):
    """
    POST /applications/<pk>/add-note/

    Adds a free-text note to the application's status timeline.

    Deliberately creates a StatusChange record with from_status == to_status (no
    actual transition) rather than calling Application.change_status(), because
    change_status() is a no-op when the status hasn't changed. The StatusChange
    model is intentionally used as the unified timeline/audit log for both status
    transitions and plain notes.
    """

    def post(self, request, pk):
        app = get_object_or_404(Application, pk=pk)
        form = AddNoteForm(request.POST)
        if form.is_valid():
            # from_status == to_status signals this is a note, not a real transition.
            StatusChange.objects.create(
                application=app,
                from_status=app.status,
                to_status=app.status,
                changed_by=request.user,
                note=form.cleaned_data["note"],
            )
            django_messages.success(request, "Note added.")
        return redirect("applications:detail", pk=pk)


class ScheduleCallbackView(LoginRequiredMixin, View):
    """POST /applications/<pk>/schedule-callback/"""

    def post(self, request, pk):
        app = get_object_or_404(Application, pk=pk)
        form = ScheduleCallbackForm(request.POST)
        if form.is_valid():
            set_callback_scheduled(
                app,
                callback_at=form.cleaned_data["callback_at"],
                changed_by=request.user,
                note=form.cleaned_data["note"] or "Callback scheduled",
            )
            django_messages.success(request, "Callback scheduled.")
        else:
            django_messages.error(request, "Invalid callback date/time.")
        return redirect("applications:detail", pk=pk)


class TriggerFollowupView(LoginRequiredMixin, View):
    """POST /applications/<pk>/trigger-followup/"""

    _TRIGGERABLE = {
        Application.Status.AWAITING_CV:   Message.MessageType.CV_FOLLOWUP_1,
        Application.Status.CV_FOLLOWUP_1: Message.MessageType.CV_FOLLOWUP_2,
        Application.Status.CV_FOLLOWUP_2: Message.MessageType.CV_FOLLOWUP_2,
    }

    def post(self, request, pk):
        app = get_object_or_404(
            Application.objects.select_related("candidate", "position"), pk=pk
        )

        if app.status not in self._TRIGGERABLE:
            django_messages.error(
                request,
                f"Cannot trigger follow-up from '{app.get_status_display()}' status.",
            )
            return redirect("applications:detail", pk=pk)

        msg_type = self._TRIGGERABLE[app.status]

        from messaging.services import send_followup
        try:
            send_followup(app, msg_type)
            django_messages.success(request, "Follow-up sent.")
        except Exception as exc:
            logger.error("Manual follow-up failed: %s", exc, exc_info=True)
            django_messages.error(request, f"Follow-up failed: {exc}")
        return redirect("applications:detail", pk=pk)


class ManualCVUploadView(LoginRequiredMixin, View):
    """POST /applications/<pk>/upload-cv/"""

    def post(self, request, pk):
        app = get_object_or_404(Application, pk=pk)
        form = ManualCVUploadForm(request.POST, request.FILES)
        if form.is_valid():
            uploaded = form.cleaned_data["cv_file"]
            handle_manual_cv_upload(app, uploaded, changed_by=request.user)

            django_messages.success(request, f"CV '{uploaded.name}' uploaded.")
        else:
            django_messages.error(request, "Invalid file upload.")
        return redirect("applications:detail", pk=pk)


class CallNowView(LoginRequiredMixin, View):
    """
    POST /applications/<pk>/call-now/

    Immediately initiate an outbound ElevenLabs call for this application,
    bypassing the scheduler queue. Useful for testing and one-off calls.
    """

    CALLABLE_STATUSES = frozenset({
        Application.Status.PENDING_CALL,
        Application.Status.CALL_QUEUED,
        Application.Status.CALL_FAILED,
        Application.Status.CALLBACK_SCHEDULED,
    })

    def post(self, request, pk):
        app = get_object_or_404(
            Application.objects.select_related("candidate", "position"), pk=pk
        )

        if app.status not in self.CALLABLE_STATUSES:
            django_messages.error(
                request,
                f"Cannot initiate call: application is in '{app.get_status_display()}' status. "
                "Only Pending Call, Call Queued, Call Failed, or Callback Scheduled applications can be called.",
            )
            return redirect("applications:detail", pk=pk)

        service = ElevenLabsService()
        try:
            call = service.initiate_outbound_call(app)
            django_messages.success(
                request,
                f"Call initiated to {app.candidate.phone} "
                f"(conversation: {call.eleven_labs_conversation_id})",
            )
        except ElevenLabsError as exc:
            logger.error("Call Now failed for application=%s: %s", pk, exc, exc_info=True)
            django_messages.error(request, f"Call failed: {exc}")

        return redirect("applications:detail", pk=pk)
