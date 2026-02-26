"""
messaging/tests.py

Covers:
  - MessageTemplate.render()         : placeholder substitution (§4.11)
  - MessageTemplate.render_subject() : subject placeholder substitution
  - CandidateReply model             : creation, str representation (§4.9)
"""

from django.test import TestCase

from candidates.models import Candidate
from messaging.models import CandidateReply, Message, MessageTemplate


# ── MessageTemplate ────────────────────────────────────────────────────────────

class MessageTemplateRenderTests(TestCase):
    def setUp(self):
        self.template = MessageTemplate.objects.create(
            message_type=MessageTemplate.MessageType.CV_REQUEST,
            channel=MessageTemplate.Channel.EMAIL,
            subject="Application for {position_title}",
            body=(
                "Dear {first_name},\n\n"
                "Thank you for applying to {position_title}. "
                "Reference: #{application_pk}."
            ),
        )

    def test_render_substitutes_all_placeholders(self):
        result = self.template.render(
            first_name="Ana",
            position_title="Sales Rep",
            application_pk=42,
        )
        self.assertIn("Ana", result)
        self.assertIn("Sales Rep", result)
        self.assertIn("42", result)
        self.assertNotIn("{first_name}", result)
        self.assertNotIn("{position_title}", result)
        self.assertNotIn("{application_pk}", result)

    def test_render_with_empty_values_leaves_blanks(self):
        result = self.template.render(
            first_name="",
            position_title="",
            application_pk="",
        )
        # Placeholders should all be replaced (with empty strings)
        self.assertNotIn("{first_name}", result)
        self.assertNotIn("{position_title}", result)
        self.assertNotIn("{application_pk}", result)

    def test_render_subject_substitutes_position_title(self):
        result = self.template.render_subject(position_title="Sales Rep")
        self.assertEqual(result, "Application for Sales Rep")

    def test_render_subject_no_placeholder_unchanged(self):
        self.template.subject = "Your application"
        self.template.save()
        result = self.template.render_subject(position_title="Sales Rep")
        self.assertEqual(result, "Your application")

    def test_render_subject_empty_title_replaces_with_blank(self):
        result = self.template.render_subject(position_title="")
        self.assertNotIn("{position_title}", result)

    def test_str_representation(self):
        self.assertIn("CV Request", str(self.template))
        self.assertIn("Email", str(self.template))

    def test_unique_together_constraint(self):
        """Only one template per (message_type, channel) pair."""
        from django.db import IntegrityError
        with self.assertRaises(IntegrityError):
            MessageTemplate.objects.create(
                message_type=MessageTemplate.MessageType.CV_REQUEST,
                channel=MessageTemplate.Channel.EMAIL,
                subject="Duplicate",
                body="Duplicate body",
            )


# ── CandidateReply ─────────────────────────────────────────────────────────────

class CandidateReplyTests(TestCase):
    def setUp(self):
        self.candidate = Candidate.objects.create(
            first_name="Ana",
            last_name="Pop",
            full_name="Ana Pop",
            phone="+40700000001",
            email="ana@example.com",
        )

    def test_create_reply_with_candidate_link(self):
        reply = CandidateReply.objects.create(
            candidate=self.candidate,
            channel=CandidateReply.Channel.WHATSAPP,
            sender="+40700000001",
            body="Hello, I have a question.",
        )
        self.assertEqual(reply.candidate, self.candidate)
        self.assertFalse(reply.is_read)
        self.assertIsNone(reply.application)

    def test_create_reply_without_candidate_link(self):
        """Replies may be created without a resolved candidate (null FK)."""
        reply = CandidateReply.objects.create(
            candidate=None,
            channel=CandidateReply.Channel.EMAIL,
            sender="unknown@example.com",
            body="Some message",
        )
        self.assertIsNone(reply.candidate)
        self.assertEqual(reply.sender, "unknown@example.com")

    def test_str_with_candidate(self):
        reply = CandidateReply.objects.create(
            candidate=self.candidate,
            channel=CandidateReply.Channel.WHATSAPP,
            sender="+40700000001",
            body="Hi",
        )
        self.assertIn("Ana Pop", str(reply))

    def test_str_without_candidate_uses_sender(self):
        reply = CandidateReply.objects.create(
            candidate=None,
            channel=CandidateReply.Channel.EMAIL,
            sender="anon@example.com",
            body="Hi",
        )
        self.assertIn("anon@example.com", str(reply))

    def test_mark_as_read(self):
        reply = CandidateReply.objects.create(
            candidate=self.candidate,
            channel=CandidateReply.Channel.WHATSAPP,
            sender="+40700000001",
            body="Unread message",
        )
        self.assertFalse(reply.is_read)
        reply.is_read = True
        reply.save()
        reply.refresh_from_db()
        self.assertTrue(reply.is_read)


# ── Message model ──────────────────────────────────────────────────────────────

class MessageModelTests(TestCase):
    def _make_application(self):
        from applications.models import Application
        from positions.models import Position
        pos = Position.objects.create(
            title="Role",
            description="Desc",
            campaign_questions="Q",
        )
        candidate = Candidate.objects.create(
            first_name="Ion",
            last_name="Ionescu",
            full_name="Ion Ionescu",
            phone="+40700000002",
            email="ion@example.com",
        )
        return Application.objects.create(candidate=candidate, position=pos)

    def test_message_defaults_to_pending_status(self):
        app = self._make_application()
        msg = Message.objects.create(
            application=app,
            channel=Message.Channel.WHATSAPP,
            message_type=Message.MessageType.CV_REQUEST,
            body="Please send your CV.",
        )
        self.assertEqual(msg.status, Message.Status.PENDING)

    def test_message_str(self):
        app = self._make_application()
        msg = Message.objects.create(
            application=app,
            channel=Message.Channel.EMAIL,
            message_type=Message.MessageType.CV_FOLLOWUP_1,
            body="Follow up body",
        )
        msg_str = str(msg)
        self.assertIn("email", msg_str)
        self.assertIn("cv_followup_1", msg_str)
