"""
prompts/views.py

CRUD + Test Generate for PromptTemplate.
Spec § 12.7.
"""

import json
import logging

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin, UserPassesTestMixin
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse_lazy
from django.views import View
from django.views.generic import CreateView, ListView, UpdateView


class _StaffRequiredMixin(LoginRequiredMixin, UserPassesTestMixin):
    """Only staff/admin users may access prompt template management."""

    def test_func(self):
        return self.request.user.is_staff

from evaluations.services import ClaudeService, ClaudeServiceError
from prompts.forms import PromptTemplateForm
from prompts.models import PromptTemplate

logger = logging.getLogger(__name__)


class PromptTemplateListView(_StaffRequiredMixin, ListView):
    model = PromptTemplate
    template_name = "prompts/prompt_list.html"
    context_object_name = "templates"

    def get_queryset(self):
        return PromptTemplate.objects.order_by("section", "-is_active", "-version")

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["active_sections"] = set(
            PromptTemplate.objects
            .filter(is_active=True)
            .exclude(section__isnull=True)
            .exclude(section="")
            .values_list("section", flat=True)
        )
        ctx["section_status"] = PromptTemplate.Section.choices
        return ctx


class PromptTemplateCreateView(_StaffRequiredMixin, CreateView):
    model = PromptTemplate
    form_class = PromptTemplateForm
    template_name = "prompts/prompt_form.html"
    success_url = reverse_lazy("prompts:list")

    def form_valid(self, form):
        last = PromptTemplate.objects.order_by("-version").first()
        form.instance.version = (last.version + 1) if last else 1
        messages.success(self.request, "Prompt template created.")
        return super().form_valid(form)


class PromptTemplateUpdateView(_StaffRequiredMixin, UpdateView):
    model = PromptTemplate
    form_class = PromptTemplateForm
    template_name = "prompts/prompt_form.html"
    success_url = reverse_lazy("prompts:list")

    def form_valid(self, form):
        form.instance.version = form.instance.version + 1
        messages.success(self.request, "Prompt template saved (new version).")
        return super().form_valid(form)


class ToggleActiveView(_StaffRequiredMixin, View):
    """
    POST /prompts/<pk>/toggle-active/

    Activating a template deactivates any other template in the SAME section.
    A section can have at most one active template at a time.
    """

    def post(self, request, pk):
        template = get_object_or_404(PromptTemplate, pk=pk)
        if not template.is_active:
            if template.section:
                # Deactivate competing templates only within the same section.
                PromptTemplate.objects.filter(
                    section=template.section, is_active=True
                ).exclude(pk=pk).update(is_active=False)
            else:
                # Legacy template without a section — deactivate all others.
                PromptTemplate.objects.filter(is_active=True).exclude(pk=pk).update(is_active=False)
            template.is_active = True
            template.save(update_fields=["is_active", "updated_at"])
            messages.success(request, f"'{template.name}' is now the active template.")
        else:
            template.is_active = False
            template.save(update_fields=["is_active", "updated_at"])
            messages.info(request, f"'{template.name}' deactivated.")
        return redirect("prompts:list")


class TestGenerateView(_StaffRequiredMixin, View):
    """
    POST /prompts/<pk>/test-generate/

    AJAX endpoint: run generate_section with sample position data against a
    specific template. Returns { "section": "...", "value": "..." } without
    saving to DB.
    """

    def post(self, request, pk):
        template = get_object_or_404(PromptTemplate, pk=pk)

        if not template.section:
            return JsonResponse(
                {"error": "This template has no section set. Please edit it and select a section first."},
                status=400,
            )

        try:
            body = json.loads(request.body)
        except (json.JSONDecodeError, ValueError):
            return JsonResponse({"error": "Invalid JSON."}, status=400)

        title = (body.get("title") or "").strip()
        if not title:
            return JsonResponse({"error": "Title is required."}, status=400)

        class _Proxy:
            pass

        proxy = _Proxy()
        proxy.pk = f"test-{pk}"
        proxy.title = title
        proxy.description = (body.get("description") or "").strip()
        proxy.campaign_questions = (body.get("campaign_questions") or "").strip()

        try:
            value = ClaudeService().generate_section(proxy, template)
        except ClaudeServiceError as exc:
            return JsonResponse({"error": str(exc)}, status=502)

        return JsonResponse({"section": template.section, "value": value})
