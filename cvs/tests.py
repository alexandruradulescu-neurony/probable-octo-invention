import tempfile

from django.test import TestCase, override_settings

from applications.models import Application
from candidates.models import Candidate
from cvs.models import CVUpload, UnmatchedInbound
from cvs.services import process_inbound_cv
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


class CVMatchingTests(TestCase):
    def setUp(self):
        self.temp_media = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_media.cleanup)

    @override_settings(MEDIA_ROOT=tempfile.gettempdir())
    def test_process_inbound_cv_exact_email_matches_and_creates_cv_upload(self):
        candidate = _make_candidate()
        position = _make_position()
        app = Application.objects.create(
            candidate=candidate,
            position=position,
            status=Application.Status.AWAITING_CV,
            qualified=True,
        )

        result = process_inbound_cv(
            channel="email",
            sender="ana@example.com",
            file_name="cv.txt",
            file_content=b"My CV content",
            text_body="Please find attached.",
            subject="Application",
            raw_payload={},
        )

        self.assertTrue(result["matched"])
        self.assertEqual(result["method"], CVUpload.MatchMethod.EXACT_EMAIL)
        app.refresh_from_db()
        self.assertEqual(app.status, Application.Status.CV_RECEIVED)
        self.assertEqual(CVUpload.objects.filter(application=app).count(), 1)

    @override_settings(MEDIA_ROOT=tempfile.gettempdir())
    def test_process_inbound_cv_no_match_creates_unmatched_inbound(self):
        result = process_inbound_cv(
            channel="email",
            sender="nobody@example.com",
            file_name="cv.txt",
            file_content=b"",
            text_body="No identifiers",
            subject="Unknown",
            raw_payload={"id": "raw1"},
        )

        self.assertFalse(result["matched"])
        self.assertIsNotNone(result["unmatched_pk"])
        self.assertTrue(UnmatchedInbound.objects.filter(pk=result["unmatched_pk"]).exists())
