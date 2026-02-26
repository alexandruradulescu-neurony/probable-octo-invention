"""
prompts/forms.py

Forms for PromptTemplate CRUD and test generation.
"""

from django import forms

from prompts.models import PromptTemplate


class PromptTemplateForm(forms.ModelForm):
    class Meta:
        model = PromptTemplate
        fields = ["section", "name", "meta_prompt"]
        widgets = {
            "section": forms.Select(attrs={"class": "form-select"}),
            "name": forms.TextInput(attrs={"class": "form-control"}),
            "meta_prompt": forms.Textarea(attrs={
                "class": "form-control font-monospace",
                "rows": 18,
                "placeholder": (
                    "Write the meta-prompt Claude will receive for this section.\n\n"
                    "Available placeholders:\n"
                    "  {title}               — position title\n"
                    "  {description}         — position description\n"
                    "  {campaign_questions}  — screening questions (one per line)\n\n"
                    "Claude will respond with plain text for the selected section only."
                ),
            }),
        }
        labels = {
            "section": "Section",
            "name": "Template Name",
            "meta_prompt": "Meta-Prompt",
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
