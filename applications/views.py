"""
applications/views.py

Application List (main daily-use screen) and Application Detail (most important screen).
Spec § 12.5.
"""

import logging

from django.contrib import messages as django_messages
from django.contrib.auth.mixins import LoginRequiredMixin
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
from calls.models import Call
from calls.services import ElevenLabsError, ElevenLabsService
from cvs.models import CVUpload
from evaluations.models import LLMEvaluation
from messaging.models import Message
from positions.models import Position

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
        ctx = super().get_context_data(**kwargs)
        ctx["positions"] = Position.objects.order_by("title")
        ctx["status_choices"] = Application.Status.choices
        ctx["current_filters"] = {
            "position": self.request.GET.get("position", ""),
            "status": self.request.GET.get("status", ""),
            "qualified": self.request.GET.get("qualified", ""),
            "date_from": self.request.GET.get("date_from", ""),
            "date_to": self.request.GET.get("date_to", ""),
        }
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
                    queryset=Call.objects.order_by("attempt_number"),
                ),
                Prefetch(
                    "evaluations",
                    queryset=LLMEvaluation.objects.order_by("-evaluated_at"),
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
            )
        )

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        app = self.object
        ctx["candidate"] = app.candidate
        ctx["position"] = app.position
        ctx["calls"] = app.calls.all()
        ctx["evaluations"] = app.evaluations.all()
        ctx["messages"] = app.messages.all()
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
                    "question": key.replace("_", " ").strip().capitalize(),
                    "answer": value,
                }
                for key, value in form_answers.items()
            ]
        else:
            ctx["form_answers_list"] = []

        return ctx


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

        apps = Application.objects.filter(
            pk__in=pks,
            status=Application.Status.PENDING_CALL,
        )
        count = apps.update(status=Application.Status.CALL_QUEUED)

        skipped = len(pks) - count
        if count:
            django_messages.success(
                request,
                f"{count} application(s) queued for calling.",
            )
        if skipped:
            django_messages.warning(
                request,
                f"{skipped} application(s) skipped (not in Pending Call status).",
            )

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
    """POST /applications/<pk>/add-note/"""

    def post(self, request, pk):
        app = get_object_or_404(Application, pk=pk)
        form = AddNoteForm(request.POST)
        if form.is_valid():
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
            app.callback_scheduled_at = form.cleaned_data["callback_at"]
            app.save(update_fields=["callback_scheduled_at", "updated_at"])
            note = form.cleaned_data["note"] or "Callback scheduled"
            app.change_status(
                Application.Status.CALLBACK_SCHEDULED,
                changed_by=request.user,
                note=note,
            )
            django_messages.success(request, "Callback scheduled.")
        else:
            django_messages.error(request, "Invalid callback date/time.")
        return redirect("applications:detail", pk=pk)


class TriggerFollowupView(LoginRequiredMixin, View):
    """POST /applications/<pk>/trigger-followup/"""

    def post(self, request, pk):
        app = get_object_or_404(
            Application.objects.select_related("candidate", "position"), pk=pk
        )
        from messaging.services import send_followup

        msg_type = Message.MessageType.CV_FOLLOWUP_1
        if app.status == Application.Status.CV_FOLLOWUP_1:
            msg_type = Message.MessageType.CV_FOLLOWUP_2

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

    def post(self, request, pk):
        app = get_object_or_404(
            Application.objects.select_related("candidate", "position"), pk=pk
        )

        service = ElevenLabsService()
        try:
            call = service.initiate_outbound_call(app)
            app.change_status(
                Application.Status.CALL_IN_PROGRESS,
                changed_by=request.user,
                note=f"Immediate call initiated (call #{call.pk})",
            )
            django_messages.success(
                request,
                f"Call initiated to {app.candidate.phone} "
                f"(conversation: {call.eleven_labs_conversation_id})",
            )
        except ElevenLabsError as exc:
            logger.error("Call Now failed for application=%s: %s", pk, exc, exc_info=True)
            django_messages.error(request, f"Call failed: {exc}")

        return redirect("applications:detail", pk=pk)
