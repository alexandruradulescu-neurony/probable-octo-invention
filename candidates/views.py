"""
candidates/views.py

Candidate List, Candidate Detail, and CSV Import (two-step flow).
Spec § 12.4 — Candidates.
"""

import csv
import io
import logging

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.db.models import Count, Q
from django.shortcuts import get_object_or_404, redirect, render
from django.views import View
from django.views.generic import DetailView, ListView

from applications.models import Application
from candidates.forms import CandidateContactForm, CandidateNoteForm, CSVImportForm
from candidates.models import Candidate
from candidates.services import import_meta_csv
from positions.models import Position

logger = logging.getLogger(__name__)

# ── Column constants (mirrored from services.py for preview) ──────────────────

_STANDARD_COLUMNS = frozenset({
    "id", "created_time", "campaign_name", "platform",
    "email", "full_name", "phone_number",
})
_IGNORED_COLUMNS = frozenset({
    "ad_id", "ad_name", "adset_id", "adset_name",
    "form_id", "form_name", "is_organic", "inbox_url",
})


class CandidateListView(LoginRequiredMixin, ListView):
    """
    Searchable, filterable candidate table.
    Filters: position, source, search (name/phone/email).
    """

    model = Candidate
    template_name = "candidates/candidate_list.html"
    context_object_name = "candidates"
    paginate_by = 50

    def get_queryset(self):
        qs = (
            Candidate.objects
            .annotate(application_count=Count("applications"))
            .order_by("-created_at")
        )

        search = self.request.GET.get("q", "").strip()
        if search:
            qs = qs.filter(
                Q(full_name__icontains=search)
                | Q(phone__icontains=search)
                | Q(email__icontains=search)
            )

        source = self.request.GET.get("source")
        if source:
            qs = qs.filter(source=source)

        position_id = self.request.GET.get("position")
        if position_id:
            qs = qs.filter(applications__position_id=position_id).distinct()

        return qs

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["positions"] = Position.objects.order_by("title")
        ctx["source_choices"] = Candidate.Source.choices
        ctx["current_filters"] = {
            "q": self.request.GET.get("q", ""),
            "source": self.request.GET.get("source", ""),
            "position": self.request.GET.get("position", ""),
        }
        return ctx


class CandidateDetailView(LoginRequiredMixin, DetailView):
    """
    Full candidate profile: contact info, Meta lead info, form answers,
    all applications with status/score, and an editable notes field.
    """

    model = Candidate
    template_name = "candidates/candidate_detail.html"
    context_object_name = "candidate"

    def get_queryset(self):
        return (
            Candidate.objects
            .prefetch_related("applications__position")
        )

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        candidate = self.object

        ctx["applications"] = (
            candidate.applications
            .select_related("position")
            .order_by("-updated_at")
        )

        form_answers = candidate.form_answers
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

        ctx["note_form"] = CandidateNoteForm(initial={"notes": candidate.notes or ""})
        ctx["contact_form"] = CandidateContactForm(instance=candidate)
        return ctx


class CandidateUpdateNotesView(LoginRequiredMixin, View):
    """POST-only: save notes on a candidate."""

    def post(self, request, pk):
        candidate = get_object_or_404(Candidate, pk=pk)
        form = CandidateNoteForm(request.POST)
        if form.is_valid():
            candidate.notes = form.cleaned_data["notes"]
            candidate.save(update_fields=["notes", "updated_at"])
            messages.success(request, "Notes saved.")
        return redirect("candidates:detail", pk=pk)


class CandidateUpdateContactView(LoginRequiredMixin, View):
    """POST-only: update candidate contact fields."""

    def post(self, request, pk):
        candidate = get_object_or_404(Candidate, pk=pk)
        form = CandidateContactForm(request.POST, instance=candidate)
        if form.is_valid():
            cand = form.save(commit=False)
            cand.full_name = f"{cand.first_name} {cand.last_name}".strip()
            cand.save(update_fields=[
                "first_name", "last_name", "full_name",
                "phone", "email", "whatsapp_number", "updated_at",
            ])
            messages.success(request, "Contact info updated.")
        else:
            for field, errors in form.errors.items():
                for error in errors:
                    messages.error(request, f"{field}: {error}")
        return redirect("candidates:detail", pk=pk)


class CSVImportView(LoginRequiredMixin, View):
    """
    Two-step CSV import flow (spec § 12.4):
      GET  → Step 1: show upload form (position selector + file input)
      POST → Step 2a (preview=true): parse CSV, show preview table
      POST → Step 2b (confirm=true): actually run the import, show results
    """

    template_name = "candidates/csv_import.html"

    def get(self, request):
        return render(request, self.template_name, {"form": CSVImportForm()})

    def post(self, request):
        if "confirm" in request.POST:
            return self._confirm(request)
        return self._preview(request)

    def _preview(self, request):
        form = CSVImportForm(request.POST, request.FILES)
        if not form.is_valid():
            return render(request, self.template_name, {"form": form})

        csv_file = request.FILES["csv_file"]
        position = form.cleaned_data["position"]

        try:
            raw = csv_file.read()
            if isinstance(raw, bytes):
                text = raw.decode("utf-16")
            else:
                text = raw

            reader = csv.DictReader(io.StringIO(text), delimiter="\t")
            rows = list(reader)
        except Exception as exc:
            logger.warning("CSV parse error: %s", exc)
            messages.error(request, f"Failed to parse CSV: {exc}")
            return render(request, self.template_name, {"form": form})

        if not rows:
            messages.warning(request, "The CSV file is empty or could not be parsed.")
            return render(request, self.template_name, {"form": form})

        preview_rows = []
        dynamic_columns = set()
        for row in rows:
            dyn = {
                col.strip(): (val or "").replace("_", " ").strip()
                for col, val in row.items()
                if col.strip() not in _STANDARD_COLUMNS
                and col.strip() not in _IGNORED_COLUMNS
                and (val or "").strip()
            }
            dynamic_columns.update(dyn.keys())
            preview_rows.append({
                "name": (row.get("full_name") or "").strip(),
                "phone": (row.get("phone_number") or "").strip(),
                "email": (row.get("email") or "").strip(),
                "campaign": (row.get("campaign_name") or "").strip(),
                "form_answers_count": len(dyn),
            })

        request.session["_csv_import_text"] = text
        request.session["_csv_import_position_id"] = position.pk

        return render(request, self.template_name, {
            "step": "preview",
            "position": position,
            "preview_rows": preview_rows[:100],
            "total_rows": len(preview_rows),
            "showing_rows": min(len(preview_rows), 100),
            "dynamic_columns": sorted(dynamic_columns),
        })

    def _confirm(self, request):
        text = request.session.pop("_csv_import_text", None)
        position_id = request.session.pop("_csv_import_position_id", None)

        if not text or not position_id:
            messages.error(request, "Import session expired. Please upload the file again.")
            return redirect("candidates:csv_import")

        try:
            file_obj = io.StringIO(text)
            summary = import_meta_csv(file_obj, position_id)
        except Exception as exc:
            logger.exception("CSV import failed")
            messages.error(request, f"Import failed: {exc}")
            return redirect("candidates:csv_import")

        position = Position.objects.filter(pk=position_id).first()

        return render(request, self.template_name, {
            "step": "result",
            "summary": summary,
            "position": position,
        })
