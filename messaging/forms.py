"""
messaging/forms.py

Forms for MessageTemplate CRUD.
"""

from django import forms

from messaging.models import MessageTemplate


class MessageTemplateForm(forms.ModelForm):
    class Meta:
        model  = MessageTemplate
        fields = ["subject", "body", "is_active"]
        widgets = {
            "subject": forms.TextInput(attrs={
                "class":       "form-control",
                "placeholder": "Email subject (leave blank for WhatsApp templates)",
            }),
            "body": forms.Textarea(attrs={
                "class": "form-control font-monospace",
                "rows":  14,
                "placeholder": (
                    "Write your message body here.\n\n"
                    "Available placeholders:\n"
                    "  {first_name}      — candidate's first name\n"
                    "  {position_title}  — job title\n"
                    "  {application_pk}  — application reference number"
                ),
            }),
            "is_active": forms.CheckboxInput(attrs={"class": "form-check-input"}),
        }
        labels = {
            "subject":   "Subject (email only)",
            "body":      "Message Body",
            "is_active": "Active — use this template when sending messages",
        }
