import tempfile
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.utils import timezone

from applications.models import Application
from applications.services import handle_manual_cv_upload
from applications.transitions import set_callback_scheduled, set_cv_received, set_needs_human
from candidates.models import Candidate
from positions.models import Position


def _make_position() -> Position:
    return Position.objects.create(
        title="Sales Rep",
        description="Role",
        campaign_questions="Q1",
        calling_hour_start=9,
        calling_hour_end=18,
        follow_up_interval_hours=24,
    )


def _make_candidate() -> Candidate:
    return Candidate.objects.create(
        first_name="Ana",
        last_name="Pop",
        full_name="Ana Pop",
        phone="+40700000001",
        email="ana@example.com",
    )


class ManualCVUploadServiceTests(TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.override = override_settings(MEDIA_ROOT=self.temp_dir.name)
        self.override.enable()
        self.user = get_user_model().objects.create_user(
            username="recruiter",
            password="test-pass-123",
        )

    def tearDown(self):
        self.override.disable()
        self.temp_dir.cleanup()

    def test_manual_upload_records_actor_and_transitions_to_cv_received(self):
        app = Application.objects.create(
            candidate=_make_candidate(),
            position=_make_position(),
            status=Application.Status.AWAITING_CV,
            qualified=True,
        )
        uploaded = SimpleUploadedFile("resume.pdf", b"%PDF-1.4 mock")

        cv_upload = handle_manual_cv_upload(app, uploaded, changed_by=self.user)

        app.refresh_from_db()
        self.assertEqual(app.status, Application.Status.CV_RECEIVED)
        self.assertIsNotNone(app.cv_received_at)
        self.assertEqual(cv_upload.application_id, app.pk)
        self.assertEqual(cv_upload.file_name, "resume.pdf")

        status_change = app.status_changes.first()
        self.assertIsNotNone(status_change)
        self.assertEqual(status_change.to_status, Application.Status.CV_RECEIVED)
        self.assertEqual(status_change.changed_by, self.user)

    def test_manual_upload_rejected_branch_transitions_to_cv_received_rejected(self):
        app = Application.objects.create(
            candidate=_make_candidate(),
            position=_make_position(),
            status=Application.Status.AWAITING_CV_REJECTED,
            qualified=False,
        )
        uploaded = SimpleUploadedFile("resume_rejected.pdf", b"%PDF-1.4 mock")

        handle_manual_cv_upload(app, uploaded, changed_by=self.user)

        app.refresh_from_db()
        self.assertEqual(app.status, Application.Status.CV_RECEIVED_REJECTED)
        self.assertIsNotNone(app.cv_received_at)
        status_change = app.status_changes.first()
        self.assertIsNotNone(status_change)
        self.assertEqual(status_change.changed_by, self.user)


class TransitionAtomicityTests(TestCase):
    def test_set_callback_scheduled_is_atomic(self):
        app = Application.objects.create(
            candidate=_make_candidate(),
            position=_make_position(),
            status=Application.Status.SCORING,
        )
        callback_at = timezone.now()

        with patch("applications.transitions.transition_status", side_effect=RuntimeError("boom")):
            with self.assertRaises(RuntimeError):
                set_callback_scheduled(app, callback_at=callback_at)

        app.refresh_from_db()
        self.assertIsNone(app.callback_scheduled_at)
        self.assertEqual(app.status, Application.Status.SCORING)

    def test_set_needs_human_is_atomic(self):
        app = Application.objects.create(
            candidate=_make_candidate(),
            position=_make_position(),
            status=Application.Status.SCORING,
        )

        with patch("applications.transitions.transition_status", side_effect=RuntimeError("boom")):
            with self.assertRaises(RuntimeError):
                set_needs_human(app, reason="Escalation required")

        app.refresh_from_db()
        self.assertIsNone(app.needs_human_reason)
        self.assertEqual(app.status, Application.Status.SCORING)

    def test_set_cv_received_is_atomic(self):
        app = Application.objects.create(
            candidate=_make_candidate(),
            position=_make_position(),
            status=Application.Status.AWAITING_CV,
            qualified=True,
        )

        with patch("applications.transitions.transition_status", side_effect=RuntimeError("boom")):
            with self.assertRaises(RuntimeError):
                set_cv_received(app, rejected=False)

        app.refresh_from_db()
        self.assertIsNone(app.cv_received_at)
        self.assertEqual(app.status, Application.Status.AWAITING_CV)
