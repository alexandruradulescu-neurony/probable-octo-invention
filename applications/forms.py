"""
applications/forms.py

Forms for Application Detail actions: manual status override,
notes, callback scheduling, follow-up trigger, and manual CV upload.
"""

from django import forms
from django.core.validators import FileExtensionValidator
from django.utils import timezone

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

    def clean_callback_at(self):
        dt = self.cleaned_data.get("callback_at")
        if dt and dt <= timezone.now():
            raise forms.ValidationError("Callback time must be in the future.")
        return dt


class ManualCVUploadForm(forms.Form):
    """Manually upload a CV file for this application."""
    MAX_CV_SIZE_MB = 10

    cv_file = forms.FileField(
        validators=[FileExtensionValidator(allowed_extensions=["pdf"])],
        widget=forms.ClearableFileInput(attrs={
            "class": "form-control form-control-sm",
            "accept": ".pdf",
        }),
    )

    def clean_cv_file(self):
        f = self.cleaned_data.get("cv_file")
        if f and f.size > self.MAX_CV_SIZE_MB * 1024 * 1024:
            raise forms.ValidationError(
                f"CV file is too large ({f.size // (1024 * 1024)} MB). "
                f"Maximum allowed size is {self.MAX_CV_SIZE_MB} MB."
            )
        return f
