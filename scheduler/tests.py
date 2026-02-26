from datetime import timedelta
from unittest.mock import patch

from django.test import TestCase
from django.utils import timezone

from applications.models import Application
from calls.models import Call
from candidates.models import Candidate
from messaging.models import Message
from positions.models import Position
from scheduler import jobs


def _make_position() -> Position:
    return Position.objects.create(
        title="Sales Rep",
        description="Role",
        campaign_questions="Q1",
        calling_hour_start=0,
        calling_hour_end=24,
        follow_up_interval_hours=1,
    )


def _make_candidate() -> Candidate:
    return Candidate.objects.create(
        first_name="Ana",
        last_name="Pop",
        full_name="Ana Pop",
        phone="+40700000001",
        email="ana@example.com",
    )


class SchedulerJobTests(TestCase):
    @patch("scheduler.jobs.ElevenLabsService.initiate_batch_calls")
    def test_process_call_queue_submits_batch_for_queued_applications(self, mock_batch):
        app = Application.objects.create(
            candidate=_make_candidate(),
            position=_make_position(),
            status=Application.Status.CALL_QUEUED,
        )
        mock_batch.return_value = [
            Call(application=app, attempt_number=1, status=Call.Status.INITIATED)
        ]

        jobs.process_call_queue.__wrapped__()

        self.assertTrue(mock_batch.called)

    @patch("scheduler.jobs.send_followup")
    def test_check_cv_followups_advances_due_application(self, mock_send_followup):
        position = _make_position()
        candidate = _make_candidate()
        app = Application.objects.create(
            candidate=candidate,
            position=position,
            status=Application.Status.AWAITING_CV,
            qualified=True,
            cv_received_at=None,
        )
        Message.objects.create(
            application=app,
            channel=Message.Channel.WHATSAPP,
            message_type=Message.MessageType.CV_REQUEST,
            status=Message.Status.SENT,
            body="hello",
            sent_at=timezone.now() - timedelta(hours=2),
        )

        jobs.check_cv_followups.__wrapped__()

        app.refresh_from_db()
        self.assertEqual(app.status, Application.Status.CV_FOLLOWUP_1)
        self.assertTrue(mock_send_followup.called)
