import json

from django.test import TestCase, override_settings
from django.urls import reverse

from applications.models import Application
from calls.models import Call
from candidates.models import Candidate
from positions.models import Position


def _make_position() -> Position:
    return Position.objects.create(
        title="Sales Rep",
        description="Role",
        campaign_questions="Q1",
    )


def _make_candidate() -> Candidate:
    return Candidate.objects.create(
        first_name="Ana",
        last_name="Pop",
        full_name="Ana Pop",
        phone="+40700000001",
        email="ana@example.com",
    )


class WebhookSecurityTests(TestCase):
    @override_settings(ELEVENLABS_WEBHOOK_SECRET="topsecret")
    def test_elevenlabs_rejects_missing_signature_when_secret_configured(self):
        response = self.client.post(
            reverse("webhooks:elevenlabs"),
            data=json.dumps({"data": {"conversation_id": "conv_x"}}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 401)
        self.assertIn("error", response.json())

    @override_settings(DEBUG=False, ELEVENLABS_WEBHOOK_SECRET="")
    def test_elevenlabs_fails_hard_in_production_when_secret_missing(self):
        response = self.client.post(
            reverse("webhooks:elevenlabs"),
            data=json.dumps({"data": {"conversation_id": "conv_x"}}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 500)
        self.assertEqual(response.json()["error"], "server_misconfigured")

    @override_settings(WHAPI_WEBHOOK_SECRET="token-1")
    def test_whapi_rejects_invalid_token(self):
        response = self.client.post(
            reverse("webhooks:whapi"),
            data=json.dumps({"messages": []}),
            content_type="application/json",
            HTTP_X_WHAPI_TOKEN="wrong",
        )
        self.assertEqual(response.status_code, 401)


class WebhookLateBindingTests(TestCase):
    @override_settings(DEBUG=True, ELEVENLABS_WEBHOOK_SECRET="")
    def test_batch_webhook_late_binds_conversation_to_initiated_call(self):
        position = _make_position()
        candidate = _make_candidate()
        application = Application.objects.create(candidate=candidate, position=position)
        call = Call.objects.create(
            application=application,
            attempt_number=1,
            status=Call.Status.INITIATED,
            eleven_labs_conversation_id=None,
        )

        payload = {
            "data": {
                "conversation_id": "conv_bound_1",
                "status": "processing",
                "conversation_initiation_client_data": {"user_id": str(application.pk)},
            }
        }
        response = self.client.post(
            reverse("webhooks:elevenlabs"),
            data=json.dumps(payload),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200)

        call.refresh_from_db()
        self.assertEqual(call.eleven_labs_conversation_id, "conv_bound_1")
