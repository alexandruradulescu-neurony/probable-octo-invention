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

    @override_settings(MEDIA_ROOT=tempfile.gettempdir())
    def test_process_inbound_cv_exact_phone_matches_whatsapp_sender(self):
        """Priority 2: exact phone match for WhatsApp sender (§11, P2)."""
        candidate = _make_candidate()
        position = _make_position()
        app = Application.objects.create(
            candidate=candidate,
            position=position,
            status=Application.Status.AWAITING_CV,
            qualified=True,
        )

        result = process_inbound_cv(
            channel="whatsapp",
            sender="+40700000001",  # matches candidate.phone
            file_name="cv.txt",
            file_content=b"CV content",
            text_body="",
            subject="",
            raw_payload={},
        )

        self.assertTrue(result["matched"])
        self.assertEqual(result["method"], CVUpload.MatchMethod.EXACT_PHONE)
        app.refresh_from_db()
        self.assertEqual(app.status, Application.Status.CV_RECEIVED)

    @override_settings(MEDIA_ROOT=tempfile.gettempdir())
    def test_process_inbound_cv_subject_id_matches_application(self):
        """Priority 3: application ID embedded in the email subject (§11, P3)."""
        candidate = _make_candidate()
        position = _make_position()
        app = Application.objects.create(
            candidate=candidate,
            position=position,
            status=Application.Status.AWAITING_CV,
            qualified=True,
        )
        subject = f"My CV — App #{app.pk}"

        result = process_inbound_cv(
            channel="email",
            sender="different_address@example.com",
            file_name="cv.txt",
            file_content=b"CV bytes",
            text_body="",
            subject=subject,
            raw_payload={},
        )

        self.assertTrue(result["matched"])
        self.assertEqual(result["method"], CVUpload.MatchMethod.SUBJECT_ID)
        app.refresh_from_db()
        self.assertEqual(app.status, Application.Status.CV_RECEIVED)

    @override_settings(MEDIA_ROOT=tempfile.gettempdir())
    def test_process_inbound_cv_fuzzy_name_match_flags_needs_review(self):
        """Priority 4: fuzzy sender display-name match sets needs_review=True (§11, P4)."""
        candidate = _make_candidate()
        position = _make_position()
        Application.objects.create(
            candidate=candidate,
            position=position,
            status=Application.Status.AWAITING_CV,
            qualified=True,
        )

        result = process_inbound_cv(
            channel="email",
            # Sender name closely matches "Ana Pop" — above FUZZY_NAME_THRESHOLD
            sender="Ana Pop <anaa.pop@gmail.com>",
            file_name="cv.txt",
            file_content=b"CV content",
            text_body="",
            subject="",
            raw_payload={},
        )

        self.assertTrue(result["matched"])
        self.assertEqual(result["method"], CVUpload.MatchMethod.FUZZY_NAME)
        self.assertEqual(result["confidence"], "medium")
        cv = CVUpload.objects.get(pk=result["cv_upload_pks"][0])
        self.assertTrue(cv.needs_review)

    @override_settings(MEDIA_ROOT=tempfile.gettempdir())
    def test_process_inbound_cv_attaches_to_all_open_applications(self):
        """Multi-application rule: same candidate + 2 open apps → both receive the CV (§11)."""
        candidate = _make_candidate()
        position_1 = _make_position()
        position_2 = Position.objects.create(
            title="Dev Role",
            description="Dev",
            campaign_questions="Q2",
        )
        app_1 = Application.objects.create(
            candidate=candidate,
            position=position_1,
            status=Application.Status.AWAITING_CV,
            qualified=True,
        )
        app_2 = Application.objects.create(
            candidate=candidate,
            position=position_2,
            status=Application.Status.AWAITING_CV,
            qualified=True,
        )

        result = process_inbound_cv(
            channel="email",
            sender="ana@example.com",
            file_name="cv.txt",
            file_content=b"CV",
            text_body="",
            subject="",
            raw_payload={},
        )

        self.assertTrue(result["matched"])
        self.assertIn(app_1.pk, result["application_pks"])
        self.assertIn(app_2.pk, result["application_pks"])
        self.assertEqual(len(result["cv_upload_pks"]), 2)
        app_1.refresh_from_db()
        app_2.refresh_from_db()
        self.assertEqual(app_1.status, Application.Status.CV_RECEIVED)
        self.assertEqual(app_2.status, Application.Status.CV_RECEIVED)

    @override_settings(MEDIA_ROOT=tempfile.gettempdir())
    def test_matched_candidate_with_no_awaiting_cv_app_falls_through_to_unmatched(self):
        """
        If the matched candidate has no applications in an awaiting-CV status,
        the cascade falls through to Priority 6 (UnmatchedInbound).
        """
        candidate = _make_candidate()
        position = _make_position()
        # Application is closed — not in an awaiting-CV status
        Application.objects.create(
            candidate=candidate,
            position=position,
            status=Application.Status.CLOSED,
            qualified=True,
        )

        result = process_inbound_cv(
            channel="email",
            sender="ana@example.com",
            file_name="cv.txt",
            file_content=b"CV",
            text_body="",
            subject="",
            raw_payload={},
        )

        self.assertFalse(result["matched"])
        self.assertIsNotNone(result["unmatched_pk"])
