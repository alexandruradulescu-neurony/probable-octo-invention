"""
webhooks/urls.py

URL patterns for inbound webhook endpoints.

  POST /webhooks/elevenlabs/  — ElevenLabs ConvAI post-call event
  POST /webhooks/whapi/       — Whapi inbound WhatsApp message

Both views are already @csrf_exempt — no additional middleware needed.
"""

from django.urls import path

from webhooks import views

app_name = "webhooks"

urlpatterns = [
    path("elevenlabs/", views.elevenlabs_webhook, name="elevenlabs"),
    path("whapi/", views.whapi_webhook, name="whapi"),
]
