"""
applications/views.py

Application List (main daily-use screen) and Application Detail (most important screen).
Spec § 12.5.
"""

from django.contrib.auth.mixins import LoginRequiredMixin
from django.db.models import Prefetch
from django.views.generic import DetailView, ListView

from applications.models import Application
from calls.models import Call
from cvs.models import CVUpload
from evaluations.models import LLMEvaluation
from messaging.models import Message
from positions.models import Position


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
