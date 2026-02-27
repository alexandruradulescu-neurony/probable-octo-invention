"""
positions/forms.py

ModelForm for Position create / edit.
"""

from django import forms

from positions.models import Position

CONTACT_TYPE_CHOICES = [
    ("cim", "CIM"),
    ("b2b", "B2B"),
]


class PositionForm(forms.ModelForm):
    # MultipleChoiceField for contact_type — stored as comma-separated string
    contact_type = forms.MultipleChoiceField(
        choices=CONTACT_TYPE_CHOICES,
        required=False,
        widget=forms.CheckboxSelectMultiple(),
        label="Type of Contact",
    )

    class Meta:
        model = Position
        fields = [
            "title",
            "company",
            "contact_type",
            "salary_range",
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
            "company": forms.TextInput(attrs={"class": "form-control", "placeholder": "e.g. Acme Corp"}),
            "salary_range": forms.TextInput(attrs={"class": "form-control", "placeholder": "e.g. 4 000 – 6 000 RON net"}),
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

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Convert stored "cim,b2b" string → list for the checkbox widget
        if self.instance and self.instance.pk and self.instance.contact_type:
            self.initial["contact_type"] = [
                v.strip() for v in self.instance.contact_type.split(",") if v.strip()
            ]

    def clean_contact_type(self):
        """Convert selected list → comma-separated string for storage."""
        values = self.cleaned_data.get("contact_type") or []
        return ",".join(values)

    def clean(self):
        cleaned = super().clean()
        start = cleaned.get("calling_hour_start")
        end = cleaned.get("calling_hour_end")
        if start is not None and end is not None and start >= end:
            raise forms.ValidationError(
                "Calling hour start must be before calling hour end."
            )
        return cleaned
