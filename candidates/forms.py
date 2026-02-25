"""
candidates/forms.py

Forms for CSV import, candidate note editing, and contact info editing.
Spec § 12.4 — Candidates.
"""

from django import forms

from candidates.models import Candidate
from positions.models import Position


class CSVImportForm(forms.Form):
    """Step 1 of CSV Import: select a target Position and upload the Meta CSV."""

    position = forms.ModelChoiceField(
        queryset=Position.objects.filter(status=Position.Status.OPEN).order_by("title"),
        widget=forms.Select(attrs={"class": "form-select"}),
        help_text="All imported candidates will be linked to this position.",
    )
    csv_file = forms.FileField(
        widget=forms.ClearableFileInput(attrs={"class": "form-control", "accept": ".csv"}),
        help_text="UTF-16 LE encoded, tab-delimited CSV exported from Meta Ads Manager.",
    )


class CandidateNoteForm(forms.Form):
    """Inline note editor on the Candidate Detail page."""

    notes = forms.CharField(
        required=False,
        widget=forms.Textarea(attrs={
            "class": "form-control",
            "rows": 3,
            "placeholder": "Add notes about this candidate\u2026",
        }),
    )


class CandidateContactForm(forms.ModelForm):
    """Inline-editable contact fields on the Candidate Detail page."""

    class Meta:
        model = Candidate
        fields = ["first_name", "last_name", "phone", "email", "whatsapp_number"]
        widgets = {
            "first_name": forms.TextInput(attrs={"class": "form-control form-control-sm"}),
            "last_name": forms.TextInput(attrs={"class": "form-control form-control-sm"}),
            "phone": forms.TextInput(attrs={"class": "form-control form-control-sm"}),
            "email": forms.EmailInput(attrs={"class": "form-control form-control-sm"}),
            "whatsapp_number": forms.TextInput(attrs={
                "class": "form-control form-control-sm",
                "placeholder": "Leave blank if same as phone",
            }),
        }
