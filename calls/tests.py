from django.test import TestCase

from applications.models import Application
from calls.models import Call
from calls.utils import apply_call_result, format_transcript, map_elevenlabs_status
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

    def test_failed_call_with_retries_remaining_schedules_callback(self):
        app = _make_app()
        # attempt 1 of 2 (default call_retry_max=2) → should schedule retry
        call = Call.objects.create(application=app, attempt_number=1, status=Call.Status.INITIATED)

        status, is_completed = apply_call_result(call, {"status": "failed"})

        self.assertFalse(is_completed)
        self.assertEqual(status, Call.Status.FAILED)
        app.refresh_from_db()
        self.assertEqual(app.status, Application.Status.CALLBACK_SCHEDULED)
        self.assertIsNotNone(app.callback_scheduled_at)

    def test_failed_call_with_retries_exhausted_sets_call_failed(self):
        app = _make_app()
        # attempt 2 of 2 → all retries exhausted → should mark call_failed
        call = Call.objects.create(application=app, attempt_number=2, status=Call.Status.INITIATED)

        status, is_completed = apply_call_result(call, {"status": "failed"})

        self.assertFalse(is_completed)
        self.assertEqual(status, Call.Status.FAILED)
        app.refresh_from_db()
        self.assertEqual(app.status, Application.Status.CALL_FAILED)

    def test_no_answer_with_retries_remaining_schedules_callback(self):
        """no_answer is treated the same as failed — schedules a retry (§9 status mapping)."""
        app = _make_app()
        call = Call.objects.create(application=app, attempt_number=1, status=Call.Status.INITIATED)

        status, is_completed = apply_call_result(call, {"status": "no_answer"})

        self.assertFalse(is_completed)
        self.assertEqual(status, Call.Status.NO_ANSWER)
        app.refresh_from_db()
        self.assertEqual(app.status, Application.Status.CALLBACK_SCHEDULED)
        self.assertIsNotNone(app.callback_scheduled_at)

    def test_already_terminal_call_is_idempotent(self):
        """Calling apply_call_result on a completed call is a no-op (duplicate webhook guard)."""
        app = _make_app()
        call = Call.objects.create(
            application=app,
            attempt_number=1,
            status=Call.Status.COMPLETED,  # already terminal
        )

        status, is_completed = apply_call_result(call, {"status": "done"})

        self.assertTrue(is_completed)
        self.assertEqual(status, Call.Status.COMPLETED)


# ── map_elevenlabs_status ──────────────────────────────────────────────────────

class MapElevenLabsStatusTests(TestCase):
    def test_done_maps_to_completed(self):
        self.assertEqual(map_elevenlabs_status("done"), Call.Status.COMPLETED)

    def test_completed_maps_to_completed(self):
        self.assertEqual(map_elevenlabs_status("completed"), Call.Status.COMPLETED)

    def test_failed_maps_to_failed(self):
        self.assertEqual(map_elevenlabs_status("failed"), Call.Status.FAILED)

    def test_no_answer_maps_to_no_answer(self):
        self.assertEqual(map_elevenlabs_status("no_answer"), Call.Status.NO_ANSWER)

    def test_busy_maps_to_busy(self):
        self.assertEqual(map_elevenlabs_status("busy"), Call.Status.BUSY)

    def test_unknown_status_defaults_to_in_progress(self):
        self.assertEqual(map_elevenlabs_status("whatever"), Call.Status.IN_PROGRESS)

    def test_empty_string_defaults_to_in_progress(self):
        self.assertEqual(map_elevenlabs_status(""), Call.Status.IN_PROGRESS)

    def test_case_insensitive(self):
        self.assertEqual(map_elevenlabs_status("DONE"), Call.Status.COMPLETED)


# ── format_transcript ──────────────────────────────────────────────────────────

class FormatTranscriptTests(TestCase):
    def test_formats_agent_and_user_turns(self):
        turns = [
            {"role": "agent", "message": "Hello, this is RecruitFlow."},
            {"role": "user", "message": "Hi, how can I help?"},
        ]
        result = format_transcript(turns)
        self.assertIn("Agent: Hello", result)
        self.assertIn("User: Hi", result)

    def test_empty_turns_returns_empty_string(self):
        self.assertEqual(format_transcript([]), "")

    def test_supports_content_field_variant(self):
        turns = [{"role": "agent", "content": "Hello via content field."}]
        result = format_transcript(turns)
        self.assertIn("Hello via content field.", result)

    def test_supports_text_field_variant(self):
        turns = [{"role": "user", "text": "Text field response."}]
        result = format_transcript(turns)
        self.assertIn("Text field response.", result)

    def test_turns_separated_by_double_newline(self):
        turns = [
            {"role": "agent", "message": "First."},
            {"role": "user", "message": "Second."},
        ]
        result = format_transcript(turns)
        self.assertIn("\n\n", result)
