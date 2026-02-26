"""
positions/tests.py

Covers:
  - Position model defaults and field behaviour (§4.1)
  - Position status choices
"""

from django.test import TestCase

from positions.models import Position


class PositionModelTests(TestCase):
    def test_create_position_with_required_fields(self):
        pos = Position.objects.create(
            title="Sales Representative",
            description="Sell stuff.",
            campaign_questions="Do you have a driving licence?",
        )
        self.assertEqual(pos.title, "Sales Representative")
        self.assertEqual(pos.status, Position.Status.OPEN)

    def test_default_status_is_open(self):
        pos = Position.objects.create(
            title="Dev",
            description="Code.",
            campaign_questions="Q1",
        )
        self.assertEqual(pos.status, Position.Status.OPEN)

    def test_default_calling_hours(self):
        pos = Position.objects.create(
            title="Test Role",
            description="Desc",
            campaign_questions="Q1",
        )
        self.assertEqual(pos.calling_hour_start, 10)
        self.assertEqual(pos.calling_hour_end, 18)

    def test_default_call_retry_max(self):
        pos = Position.objects.create(
            title="Test Role",
            description="Desc",
            campaign_questions="Q1",
        )
        self.assertEqual(pos.call_retry_max, 2)

    def test_default_rejected_cv_timeout_days(self):
        pos = Position.objects.create(
            title="Test Role",
            description="Desc",
            campaign_questions="Q1",
        )
        self.assertEqual(pos.rejected_cv_timeout_days, 7)

    def test_str_representation_includes_title(self):
        pos = Position.objects.create(
            title="Backend Engineer",
            description="Desc",
            campaign_questions="Q1",
        )
        self.assertIn("Backend Engineer", str(pos))

    def test_optional_prompt_fields_are_nullable(self):
        pos = Position.objects.create(
            title="Minimal Role",
            description="Desc",
            campaign_questions="Q1",
        )
        self.assertIsNone(pos.system_prompt)
        self.assertIsNone(pos.first_message)
        self.assertIsNone(pos.qualification_prompt)

    def test_status_choices_are_valid(self):
        valid_statuses = {c[0] for c in Position.Status.choices}
        self.assertIn("open", valid_statuses)
        self.assertIn("paused", valid_statuses)
        self.assertIn("closed", valid_statuses)
