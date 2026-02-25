"""
positions/forms.py

ModelForm for Position create / edit.
"""

from django import forms

from positions.models import Position


class PositionForm(forms.ModelForm):
    class Meta:
        model = Position
        fields = [
            "title",
            "description",
            "status",
            "campaign_questions",
            "system_prompt",
            "first_message",
            "qualification_prompt",
            "call_retry_max",
            "call_retry_interval_minutes",
            "calling_hour_start",
            "calling_hour_end",
            "follow_up_interval_hours",
            "rejected_cv_timeout_days",
        ]
        widgets = {
            "title": forms.TextInput(attrs={"class": "form-control"}),
            "description": forms.Textarea(attrs={"class": "form-control", "rows": 4}),
            "status": forms.Select(attrs={"class": "form-select"}),
            "campaign_questions": forms.Textarea(
                attrs={"class": "form-control", "rows": 6,
                       "placeholder": "One screening question per line"}
            ),
            "system_prompt": forms.Textarea(attrs={"class": "form-control", "rows": 8}),
            "first_message": forms.Textarea(attrs={"class": "form-control", "rows": 4}),
            "qualification_prompt": forms.Textarea(attrs={"class": "form-control", "rows": 8}),
            "call_retry_max": forms.NumberInput(attrs={"class": "form-control", "min": 0}),
            "call_retry_interval_minutes": forms.NumberInput(attrs={"class": "form-control", "min": 1}),
            "calling_hour_start": forms.NumberInput(attrs={"class": "form-control", "min": 0, "max": 23}),
            "calling_hour_end": forms.NumberInput(attrs={"class": "form-control", "min": 0, "max": 23}),
            "follow_up_interval_hours": forms.NumberInput(attrs={"class": "form-control", "min": 1}),
            "rejected_cv_timeout_days": forms.NumberInput(attrs={"class": "form-control", "min": 1}),
        }

    def clean(self):
        cleaned = super().clean()
        start = cleaned.get("calling_hour_start")
        end = cleaned.get("calling_hour_end")
        if start is not None and end is not None and start >= end:
            raise forms.ValidationError(
                "Calling hour start must be before calling hour end."
            )
        return cleaned
