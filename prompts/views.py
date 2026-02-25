"""
prompts/views.py

CRUD + Test Generate for PromptTemplate.
Spec § 12.7.
"""

import json
import logging

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse_lazy
from django.views import View
from django.views.generic import CreateView, ListView, UpdateView

from evaluations.services import ClaudeService, ClaudeServiceError
from prompts.forms import PromptTemplateForm
from prompts.models import PromptTemplate

logger = logging.getLogger(__name__)


class PromptTemplateListView(LoginRequiredMixin, ListView):
    model = PromptTemplate
    template_name = "prompts/prompt_list.html"
    context_object_name = "templates"

    def get_queryset(self):
        return PromptTemplate.objects.order_by("-is_active", "-version")


class PromptTemplateCreateView(LoginRequiredMixin, CreateView):
    model = PromptTemplate
    form_class = PromptTemplateForm
    template_name = "prompts/prompt_form.html"
    success_url = reverse_lazy("prompts:list")

    def form_valid(self, form):
        last = PromptTemplate.objects.order_by("-version").first()
        form.instance.version = (last.version + 1) if last else 1
        messages.success(self.request, "Prompt template created.")
        return super().form_valid(form)


class PromptTemplateUpdateView(LoginRequiredMixin, UpdateView):
    model = PromptTemplate
    form_class = PromptTemplateForm
    template_name = "prompts/prompt_form.html"
    success_url = reverse_lazy("prompts:list")

    def form_valid(self, form):
        form.instance.version = form.instance.version + 1
        messages.success(self.request, "Prompt template saved (new version).")
        return super().form_valid(form)


class ToggleActiveView(LoginRequiredMixin, View):
    """POST /prompts/<pk>/toggle-active/"""

    def post(self, request, pk):
        template = get_object_or_404(PromptTemplate, pk=pk)
        if not template.is_active:
            PromptTemplate.objects.filter(is_active=True).update(is_active=False)
            template.is_active = True
            template.save(update_fields=["is_active", "updated_at"])
            messages.success(request, f"'{template.name}' is now the active template.")
        else:
            template.is_active = False
            template.save(update_fields=["is_active", "updated_at"])
            messages.info(request, f"'{template.name}' deactivated.")
        return redirect("prompts:list")


class TestGenerateView(LoginRequiredMixin, View):
    """
    POST /prompts/<pk>/test-generate/

    AJAX endpoint: run generate_prompts with sample position data
    against a specific template. Returns JSON output without saving.
    """

    def post(self, request, pk):
        template = get_object_or_404(PromptTemplate, pk=pk)

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
            result = ClaudeService().generate_prompts(proxy, template)
        except ClaudeServiceError as exc:
            return JsonResponse({"error": str(exc)}, status=502)

        return JsonResponse(result)
