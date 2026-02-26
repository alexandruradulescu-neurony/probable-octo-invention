"""
positions/views.py

CRUD views for Position management + AJAX prompt generation.
Spec § 12.3.
"""

import json
import logging

from django.contrib.auth.mixins import LoginRequiredMixin
from django.db.models import Count, Q
from django.http import JsonResponse
from django.shortcuts import redirect
from django.urls import reverse_lazy
from django.views import View
from django.views.generic import CreateView, ListView, UpdateView

from evaluations.services import ClaudeService, ClaudeServiceError
from positions.forms import PositionForm
from positions.models import Position
from prompts.models import PromptTemplate

logger = logging.getLogger(__name__)


class PositionListView(LoginRequiredMixin, ListView):
    """
    Table of all positions.
    Columns: title, status badge, open applications count, created date.
    """
    model = Position
    template_name = "positions/position_list.html"
    context_object_name = "positions"

    def get_queryset(self):
        return (
            Position.objects
            .annotate(
                open_applications_count=Count(
                    "applications",
                    filter=~Q(applications__status="closed"),
                )
            )
            .order_by("-created_at")
        )


class BulkDeletePositionsView(LoginRequiredMixin, View):
    """POST /positions/bulk-delete/ — permanently delete selected positions + their applications."""

    def post(self, request):
        from django.contrib import messages
        pks = request.POST.getlist("position_ids")
        if not pks:
            messages.warning(request, "No positions selected.")
            return redirect("positions:list")
        count, _ = Position.objects.filter(pk__in=pks).delete()
        messages.success(request, f"Deleted {count} record(s) (positions and related applications).")
        return redirect("positions:list")


class PositionCreateView(LoginRequiredMixin, CreateView):
    """
    Create a new position.
    Spec § 12.3 — form fields match the Position model.
    """
    model = Position
    form_class = PositionForm
    template_name = "positions/position_form.html"
    success_url = reverse_lazy("positions:list")


class PositionUpdateView(LoginRequiredMixin, UpdateView):
    """
    Edit an existing position.
    """
    model = Position
    form_class = PositionForm
    template_name = "positions/position_form.html"
    success_url = reverse_lazy("positions:list")


class GeneratePromptsView(LoginRequiredMixin, View):
    """
    POST /positions/generate-prompts/

    AJAX endpoint: accepts position field values in request body,
    calls ClaudeService.generate_prompts() with the active PromptTemplate,
    and returns the three generated prompt fields as JSON.
    """

    def post(self, request):
        try:
            body = json.loads(request.body)
        except (json.JSONDecodeError, ValueError):
            return JsonResponse({"error": "Invalid JSON body."}, status=400)

        title = (body.get("title") or "").strip()
        description = (body.get("description") or "").strip()
        campaign_questions = (body.get("campaign_questions") or "").strip()

        if not title:
            return JsonResponse({"error": "Position title is required."}, status=400)

        template = PromptTemplate.objects.filter(is_active=True).first()
        if not template:
            return JsonResponse(
                {"error": "No active Prompt Template configured. Create one first."},
                status=400,
            )

        class _PositionProxy:
            """Lightweight object mirroring Position fields for the service."""
            pass

        proxy = _PositionProxy()
        proxy.pk = body.get("pk", "new")
        proxy.title = title
        proxy.description = description
        proxy.campaign_questions = campaign_questions

        try:
            result = ClaudeService().generate_prompts(proxy, template)
        except ClaudeServiceError as exc:
            logger.error("Generate prompts failed: %s", exc)
            return JsonResponse({"error": str(exc)}, status=502)

        return JsonResponse(result)
