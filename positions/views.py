"""
positions/views.py

CRUD views for Position management.
Spec § 12.3.
"""

from django.contrib.auth.mixins import LoginRequiredMixin
from django.db.models import Count, Q
from django.urls import reverse_lazy
from django.views.generic import CreateView, ListView, UpdateView

from positions.forms import PositionForm
from positions.models import Position


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
