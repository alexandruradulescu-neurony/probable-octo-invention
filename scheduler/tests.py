from datetime import timedelta
from unittest.mock import patch

from django.test import TestCase
from django.utils import timezone

from applications.models import Application, StatusChange
from calls.models import Call
from calls.services import ElevenLabsError
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

    @patch("scheduler.jobs.ElevenLabsService.initiate_batch_calls", side_effect=ElevenLabsError("boom"))
    def test_process_call_queue_batch_failure_marks_call_failed_with_audit(self, mock_batch):
        position = _make_position()
        app_1 = Application.objects.create(
            candidate=_make_candidate(),
            position=position,
            status=Application.Status.CALL_QUEUED,
        )
        app_2 = Application.objects.create(
            candidate=_make_candidate(),
            position=position,
            status=Application.Status.CALL_QUEUED,
        )

        jobs.process_call_queue.__wrapped__()

        app_1.refresh_from_db()
        app_2.refresh_from_db()
        self.assertEqual(app_1.status, Application.Status.CALL_FAILED)
        self.assertEqual(app_2.status, Application.Status.CALL_FAILED)
        self.assertEqual(mock_batch.call_count, 1)
        self.assertEqual(
            StatusChange.objects.filter(
                application_id__in=[app_1.pk, app_2.pk],
                from_status=Application.Status.CALL_QUEUED,
                to_status=Application.Status.CALL_FAILED,
            ).count(),
            2,
        )

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

    @patch("scheduler.jobs.send_followup")
    def test_check_cv_followups_does_not_advance_not_due_application(self, mock_send_followup):
        """Application whose interval has not elapsed should be left alone."""
        position = _make_position()
        app = Application.objects.create(
            candidate=_make_candidate(),
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
            sent_at=timezone.now() - timedelta(minutes=10),  # interval is 1h
        )

        jobs.check_cv_followups.__wrapped__()

        app.refresh_from_db()
        self.assertEqual(app.status, Application.Status.AWAITING_CV)
        self.assertFalse(mock_send_followup.called)

    @patch("scheduler.jobs.send_followup")
    def test_check_cv_followups_skips_not_qualified_applications(self, mock_send_followup):
        """Rejected-path applications (qualified=False) must never receive follow-ups (§6)."""
        position = _make_position()
        app = Application.objects.create(
            candidate=_make_candidate(),
            position=position,
            status=Application.Status.AWAITING_CV_REJECTED,
            qualified=False,
            cv_received_at=None,
        )
        Message.objects.create(
            application=app,
            channel=Message.Channel.WHATSAPP,
            message_type=Message.MessageType.CV_REQUEST_REJECTED,
            status=Message.Status.SENT,
            body="hello",
            sent_at=timezone.now() - timedelta(hours=2),
        )

        jobs.check_cv_followups.__wrapped__()

        app.refresh_from_db()
        # Status must remain unchanged — rejected apps are never followed up.
        self.assertEqual(app.status, Application.Status.AWAITING_CV_REJECTED)
        self.assertFalse(mock_send_followup.called)

    def test_close_stale_rejected_closes_awaiting_cv_rejected_past_deadline(self):
        """
        close_stale_rejected must close AWAITING_CV_REJECTED apps whose timeout
        has elapsed (spec §6 — close_stale_rejected case 1).
        """
        position = Position.objects.create(
            title="Sales Rep",
            description="Role",
            campaign_questions="Q1",
            calling_hour_start=0,
            calling_hour_end=24,
            follow_up_interval_hours=1,
            rejected_cv_timeout_days=7,
        )
        app = Application.objects.create(
            candidate=_make_candidate(),
            position=position,
            status=Application.Status.AWAITING_CV_REJECTED,
            qualified=False,
            cv_received_at=None,
        )
        # Simulate a status change that happened 8 days ago (past the 7-day timeout).
        # auto_now_add=True means we must use update() to backdated changed_at.
        sc = StatusChange.objects.create(
            application=app,
            from_status=Application.Status.NOT_QUALIFIED,
            to_status=Application.Status.AWAITING_CV_REJECTED,
        )
        StatusChange.objects.filter(pk=sc.pk).update(
            changed_at=timezone.now() - timedelta(days=8)
        )

        jobs.close_stale_rejected.__wrapped__()

        app.refresh_from_db()
        self.assertEqual(app.status, Application.Status.CLOSED)

    def test_close_stale_rejected_leaves_recent_application_open(self):
        """Applications within their timeout window must not be closed."""
        position = Position.objects.create(
            title="Role 2",
            description="Role",
            campaign_questions="Q1",
            calling_hour_start=0,
            calling_hour_end=24,
            follow_up_interval_hours=1,
            rejected_cv_timeout_days=7,
        )
        app = Application.objects.create(
            candidate=_make_candidate(),
            position=position,
            status=Application.Status.AWAITING_CV_REJECTED,
            qualified=False,
            cv_received_at=None,
        )
        # Transition happened only 2 days ago — still within the 7-day window.
        sc = StatusChange.objects.create(
            application=app,
            from_status=Application.Status.NOT_QUALIFIED,
            to_status=Application.Status.AWAITING_CV_REJECTED,
        )
        StatusChange.objects.filter(pk=sc.pk).update(
            changed_at=timezone.now() - timedelta(days=2)
        )

        jobs.close_stale_rejected.__wrapped__()

        app.refresh_from_db()
        self.assertEqual(app.status, Application.Status.AWAITING_CV_REJECTED)

    @patch("scheduler.jobs.ElevenLabsService.initiate_batch_calls")
    def test_process_call_queue_skips_applications_outside_calling_hours(self, mock_batch):
        """
        Applications whose Position.calling_hour_start–end window excludes the
        current hour must not be submitted to ElevenLabs (spec §6 + §10 step 3).
        """
        position = Position.objects.create(
            title="Strict Hours Role",
            description="Role",
            campaign_questions="Q1",
            calling_hour_start=10,
            calling_hour_end=11,   # Only hour 10 is valid
        )
        Application.objects.create(
            candidate=_make_candidate(),
            position=position,
            status=Application.Status.CALL_QUEUED,
        )

        # Patch timezone.now() to return a time at hour 20 (outside the window)
        fake_now = timezone.now().replace(hour=20, minute=0, second=0, microsecond=0)
        with patch("scheduler.jobs.timezone.now", return_value=fake_now):
            jobs.process_call_queue.__wrapped__()

        # Batch should NOT have been called — all apps were outside calling hours
        mock_batch.assert_not_called()
