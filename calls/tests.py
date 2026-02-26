from django.test import TestCase

from applications.models import Application
from calls.models import Call
from calls.utils import apply_call_result
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


def _make_app() -> Application:
    return Application.objects.create(candidate=_make_candidate(), position=_make_position())


class ApplyCallResultTests(TestCase):
    def test_completed_call_sets_scoring_and_returns_completed_flag(self):
        app = _make_app()
        call = Call.objects.create(application=app, attempt_number=1, status=Call.Status.INITIATED)

        status, is_completed = apply_call_result(
            call,
            {
                "status": "done",
                "transcript": [{"role": "agent", "message": "Salut"}],
                "analysis": {"transcript_summary": "ok", "call_summary_title": "summary"},
                "metadata": {"call_duration_secs": 30},
            },
        )

        self.assertTrue(is_completed)
        self.assertEqual(status, Call.Status.COMPLETED)
        call.refresh_from_db()
        app.refresh_from_db()
        self.assertEqual(app.status, Application.Status.SCORING)
        self.assertEqual(call.duration_seconds, 30)
        self.assertIn("Agent: Salut", call.transcript)

    def test_failed_call_sets_application_call_failed(self):
        app = _make_app()
        call = Call.objects.create(application=app, attempt_number=1, status=Call.Status.INITIATED)

        status, is_completed = apply_call_result(call, {"status": "failed"})

        self.assertFalse(is_completed)
        self.assertEqual(status, Call.Status.FAILED)
        app.refresh_from_db()
        self.assertEqual(app.status, Application.Status.CALL_FAILED)
