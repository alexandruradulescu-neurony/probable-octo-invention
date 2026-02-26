"""
applications/forms.py

Forms for Application Detail actions: manual status override,
notes, callback scheduling, follow-up trigger, and manual CV upload.
"""

from django import forms
from django.core.validators import FileExtensionValidator

from applications.models import Application


class StatusOverrideForm(forms.Form):
    """Manual status change with reason."""
    new_status = forms.ChoiceField(
        choices=Application.Status.choices,
        widget=forms.Select(attrs={"class": "form-select form-select-sm"}),
    )
    reason = forms.CharField(
        required=False,
        widget=forms.TextInput(attrs={
            "class": "form-control form-control-sm",
            "placeholder": "Reason for override…",
        }),
    )


class AddNoteForm(forms.Form):
    """Add a note to the application's timeline."""
    note = forms.CharField(
        widget=forms.Textarea(attrs={
            "class": "form-control form-control-sm",
            "rows": 2,
            "placeholder": "Add a note…",
        }),
    )


class ScheduleCallbackForm(forms.Form):
    """Schedule a callback for the candidate."""
    callback_at = forms.DateTimeField(
        widget=forms.DateTimeInput(attrs={
            "class": "form-control form-control-sm",
            "type": "datetime-local",
        }),
    )
    note = forms.CharField(
        required=False,
        widget=forms.TextInput(attrs={
            "class": "form-control form-control-sm",
            "placeholder": "Callback reason…",
        }),
    )


class ManualCVUploadForm(forms.Form):
    """Manually upload a CV file for this application."""
    cv_file = forms.FileField(
        validators=[FileExtensionValidator(allowed_extensions=["pdf"])],
        widget=forms.ClearableFileInput(attrs={
            "class": "form-control form-control-sm",
            "accept": ".pdf",
        }),
    )
