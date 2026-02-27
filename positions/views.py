"""
positions/views.py

CRUD views for Position management + AJAX prompt generation.
Spec § 12.3.
"""

import json
import logging

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.cache import cache
from django.db.models import Count, Q
from django.http import JsonResponse
from django.shortcuts import redirect
from django.urls import reverse_lazy
from django.utils import timezone
from django.views import View
from django.views.generic import CreateView, ListView, UpdateView

from recruitflow.constants import SIDEBAR_CACHE_KEY

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
                ),
                total_applications_count=Count("applications"),
                qualified_count=Count(
                    "applications",
                    filter=Q(applications__qualified=True),
                ),
            )
            .order_by("-created_at")
        )


class BulkDeletePositionsView(LoginRequiredMixin, View):
    """POST /positions/bulk-delete/ — permanently delete selected positions + their applications."""

    def post(self, request):
        pks = request.POST.getlist("position_ids")
        if not pks:
            messages.warning(request, "No positions selected.")
            return redirect("positions:list")
        confirm = request.POST.get("confirm_delete", "").strip().lower()
        if confirm != "yes":
            messages.error(
                request,
                "Delete not confirmed. Please check the confirmation checkbox before deleting.",
            )
            return redirect("positions:list")
        count, _ = Position.objects.filter(pk__in=pks).delete()
        cache.delete(SIDEBAR_CACHE_KEY)
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


class GenerateSectionView(LoginRequiredMixin, View):
    """
    POST /positions/generate-section/

    AJAX endpoint: generates a single prompt section via Claude.

    Request body (JSON):
      {
        "section"           : "system_prompt" | "first_message" | "qualification_prompt",
        "title"             : "...",
        "description"       : "...",          (optional)
        "campaign_questions": "...",          (optional)
        "position_pk"       : <int>           (optional — if provided, saves field to DB)
      }

    Response (JSON):
      { "section": "system_prompt", "value": "..." }

    If ``position_pk`` is a valid integer, the generated value is persisted to
    the corresponding Position field immediately so the user never loses work.
    """

    VALID_SECTIONS = {"system_prompt", "first_message", "qualification_prompt"}

    def post(self, request):
        try:
            body = json.loads(request.body)
        except (json.JSONDecodeError, ValueError):
            return JsonResponse({"error": "Invalid JSON body."}, status=400)

        section = (body.get("section") or "").strip()
        if section not in self.VALID_SECTIONS:
            return JsonResponse(
                {"error": f"Unknown section '{section}'. Must be one of: {', '.join(sorted(self.VALID_SECTIONS))}"},
                status=400,
            )

        title = (body.get("title") or "").strip()
        if not title:
            return JsonResponse({"error": "Position title is required."}, status=400)

        template = PromptTemplate.objects.filter(section=section, is_active=True).first()
        if not template:
            return JsonResponse(
                {
                    "error": (
                        f"No active prompt template for section '{section}'. "
                        "Please create and activate one under Templates → AI Prompts."
                    )
                },
                status=400,
            )

        class _PositionProxy:
            pass

        proxy = _PositionProxy()
        proxy.pk = body.get("position_pk", "new")
        proxy.title = title
        proxy.company = (body.get("company") or "").strip()
        proxy.contact_type = (body.get("contact_type") or "").strip()
        proxy.salary_range = (body.get("salary_range") or "").strip()
        proxy.description = (body.get("description") or "").strip()
        proxy.campaign_questions = (body.get("campaign_questions") or "").strip()

        try:
            value = ClaudeService().generate_section(proxy, template)
        except ClaudeServiceError as exc:
            logger.error("Generate section %s failed: %s", section, exc)
            return JsonResponse({"error": str(exc)}, status=502)

        # Auto-save to DB when editing an existing Position
        position_pk = body.get("position_pk")
        if position_pk and str(position_pk).lstrip("-").isdigit() and int(position_pk) > 0:
            rows = Position.objects.filter(pk=int(position_pk)).update(
                **{section: value},
                updated_at=timezone.now(),
            )
            if rows:
                logger.info(
                    "Auto-saved section=%s to position=%s (%d chars)",
                    section, position_pk, len(value),
                )
            else:
                logger.warning(
                    "Auto-save skipped: position=%s not found in DB", position_pk,
                )

        return JsonResponse({"section": section, "value": value})
