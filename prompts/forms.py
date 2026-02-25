"""
prompts/forms.py

Forms for PromptTemplate CRUD and test generation.
"""

from django import forms

from prompts.models import PromptTemplate


class PromptTemplateForm(forms.ModelForm):
    class Meta:
        model = PromptTemplate
        fields = ["name", "meta_prompt"]
        widgets = {
            "name": forms.TextInput(attrs={"class": "form-control"}),
            "meta_prompt": forms.Textarea(attrs={
                "class": "form-control font-monospace",
                "rows": 18,
                "placeholder": (
                    "Write the meta-prompt sent to Claude.\n\n"
                    "Available placeholders: {title}, {description}, {campaign_questions}\n\n"
                    "Claude must return JSON with keys: system_prompt, first_message, qualification_prompt"
                ),
            }),
        }


class TestGenerateForm(forms.Form):
    """Sample position data for previewing prompt generation output."""
    title = forms.CharField(
        widget=forms.TextInput(attrs={
            "class": "form-control form-control-sm",
            "placeholder": "Sample Position Title",
        }),
    )
    description = forms.CharField(
        required=False,
        widget=forms.Textarea(attrs={
            "class": "form-control form-control-sm",
            "rows": 3,
            "placeholder": "Sample description…",
        }),
    )
    campaign_questions = forms.CharField(
        required=False,
        widget=forms.Textarea(attrs={
            "class": "form-control form-control-sm",
            "rows": 3,
            "placeholder": "Sample campaign questions (one per line)…",
        }),
    )
