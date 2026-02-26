import json
from unittest.mock import patch

from django.test import TestCase

from applications.models import Application
from calls.models import Call
from candidates.models import Candidate
from evaluations.models import LLMEvaluation
from evaluations.services import ClaudeService
from positions.models import Position


def _make_position() -> Position:
    return Position.objects.create(
        title="Sales Rep",
        description="Role",
        campaign_questions="Q1",
        qualification_prompt="Evaluate candidate.",
    )


def _make_candidate() -> Candidate:
    return Candidate.objects.create(
        first_name="Ana",
        last_name="Pop",
        full_name="Ana Pop",
        phone="+40700000001",
        email="ana@example.com",
    )


def _make_call() -> Call:
    app = Application.objects.create(candidate=_make_candidate(), position=_make_position())
    return Call.objects.create(
        application=app,
        attempt_number=1,
        status=Call.Status.COMPLETED,
        transcript="Agent: Salut\n\nUser: Buna",
    )


class ClaudeEvaluationTests(TestCase):
    @patch.object(ClaudeService, "_trigger_cv_request")
    @patch.object(ClaudeService, "_send_message")
    def test_evaluate_call_qualified_updates_application(self, mock_send_message, _mock_trigger):
        mock_send_message.return_value = json.dumps(
            {
                "outcome": "qualified",
                "qualified": True,
                "score": 91,
                "reasoning": "Good fit",
                "callback_requested": False,
                "callback_notes": None,
                "needs_human": False,
                "needs_human_notes": None,
                "callback_at": None,
            }
        )
        call = _make_call()

        evaluation = ClaudeService().evaluate_call(call)

        self.assertEqual(evaluation.outcome, LLMEvaluation.Outcome.QUALIFIED)
        call.application.refresh_from_db()
        self.assertEqual(call.application.status, Application.Status.QUALIFIED)
        self.assertEqual(call.application.score, 91)

    @patch.object(ClaudeService, "_send_message")
    def test_evaluate_call_callback_requested_sets_callback_status(self, mock_send_message):
        mock_send_message.return_value = json.dumps(
            {
                "outcome": "callback_requested",
                "qualified": False,
                "score": 50,
                "reasoning": "Requested callback",
                "callback_requested": True,
                "callback_notes": "Tomorrow morning",
                "needs_human": False,
                "needs_human_notes": None,
                "callback_at": "2030-01-01T09:00:00Z",
            }
        )
        call = _make_call()

        ClaudeService().evaluate_call(call)

        call.application.refresh_from_db()
        self.assertEqual(call.application.status, Application.Status.CALLBACK_SCHEDULED)
        self.assertIsNotNone(call.application.callback_scheduled_at)
