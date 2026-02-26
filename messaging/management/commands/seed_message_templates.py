"""
management/commands/seed_message_templates.py

Populates default MessageTemplate rows for every MessageType × Channel
combination. Safe to run multiple times — uses get_or_create so existing
custom templates are never overwritten.

Usage:
    python manage.py seed_message_templates
    python manage.py seed_message_templates --force   # overwrite existing bodies
"""

from django.core.management.base import BaseCommand

from messaging.models import MessageTemplate

MT = MessageTemplate.MessageType
CH = MessageTemplate.Channel

DEFAULTS = [
    # ── CV Request (Qualified) ──────────────────────────────────────────────
    {
        "message_type": MT.CV_REQUEST,
        "channel": CH.WHATSAPP,
        "subject": "",
        "body": (
            "Hi {first_name},\n\n"
            "Great news! Following your recent call about the {position_title} position, "
            "we'd love to move forward with your application.\n\n"
            "Please send us your CV at your earliest convenience.\n\n"
            "Your application reference is #{application_pk}.\n\n"
            "Thank you!"
        ),
    },
    {
        "message_type": MT.CV_REQUEST,
        "channel": CH.EMAIL,
        "subject": "CV Request — {position_title}",
        "body": (
            "Hi {first_name},\n\n"
            "Great news! Following your recent call about the {position_title} position, "
            "we'd like to move forward with your application.\n\n"
            "Could you please send us your CV/resume at your earliest convenience?\n\n"
            "Your application reference is #{application_pk}.\n\n"
            "Thank you!\n"
            "The {position_title} Recruitment Team"
        ),
    },
    # ── CV Request (Not Qualified) ──────────────────────────────────────────
    {
        "message_type": MT.CV_REQUEST_REJECTED,
        "channel": CH.WHATSAPP,
        "subject": "",
        "body": (
            "Hi {first_name},\n\n"
            "Thank you for speaking with us about the {position_title} position. "
            "While this role may not be the best fit right now, we'd love to keep "
            "your details on file for future opportunities.\n\n"
            "Feel free to send us your CV if you'd like to stay in touch.\n\n"
            "Best regards!"
        ),
    },
    {
        "message_type": MT.CV_REQUEST_REJECTED,
        "channel": CH.EMAIL,
        "subject": "Thank you — {position_title}",
        "body": (
            "Hi {first_name},\n\n"
            "Thank you for your interest in the {position_title} position and for taking "
            "the time to speak with us.\n\n"
            "While this particular role may not be the best fit right now, we'd love to "
            "keep your details on file for future opportunities. If you'd like, please "
            "send us your CV/resume.\n\n"
            "Best regards,\n"
            "The Recruitment Team"
        ),
    },
    # ── CV Follow-up 1 ──────────────────────────────────────────────────────
    {
        "message_type": MT.CV_FOLLOWUP_1,
        "channel": CH.WHATSAPP,
        "subject": "",
        "body": (
            "Hi {first_name}, just a gentle reminder — "
            "we're still waiting for your CV for the {position_title} role. "
            "Please send it at your earliest convenience. 😊"
        ),
    },
    {
        "message_type": MT.CV_FOLLOWUP_1,
        "channel": CH.EMAIL,
        "subject": "Reminder: CV for {position_title}",
        "body": (
            "Hi {first_name},\n\n"
            "Just a gentle reminder that we're still waiting for your CV "
            "for the {position_title} role.\n\n"
            "Please send it at your earliest convenience so we can keep your "
            "application moving forward.\n\n"
            "Best regards,\n"
            "The Recruitment Team"
        ),
    },
    # ── CV Follow-up 2 ──────────────────────────────────────────────────────
    {
        "message_type": MT.CV_FOLLOWUP_2,
        "channel": CH.WHATSAPP,
        "subject": "",
        "body": (
            "Hi {first_name}, this is a final reminder regarding "
            "your CV for the {position_title} position. "
            "Please send it as soon as possible so we can continue with your application."
        ),
    },
    {
        "message_type": MT.CV_FOLLOWUP_2,
        "channel": CH.EMAIL,
        "subject": "Final Reminder: CV for {position_title}",
        "body": (
            "Hi {first_name},\n\n"
            "This is a final reminder regarding your CV for the {position_title} position.\n\n"
            "Please send it as soon as possible so we can continue processing your application. "
            "If we don't hear from you shortly we may need to close your file.\n\n"
            "Best regards,\n"
            "The Recruitment Team"
        ),
    },
    # ── Rejection ───────────────────────────────────────────────────────────
    {
        "message_type": MT.REJECTION,
        "channel": CH.WHATSAPP,
        "subject": "",
        "body": (
            "Hi {first_name}, thank you for applying for the {position_title} position. "
            "After careful consideration, we've decided to move forward with other candidates. "
            "We wish you all the best in your job search!"
        ),
    },
    {
        "message_type": MT.REJECTION,
        "channel": CH.EMAIL,
        "subject": "Your application — {position_title}",
        "body": (
            "Hi {first_name},\n\n"
            "Thank you for taking the time to apply for the {position_title} position "
            "and for speaking with us.\n\n"
            "After careful consideration, we've decided to move forward with other candidates "
            "whose experience more closely matches our current needs.\n\n"
            "We wish you the very best in your job search and future career.\n\n"
            "Kind regards,\n"
            "The Recruitment Team"
        ),
    },
]


class Command(BaseCommand):
    help = "Seed default MessageTemplate rows (one per MessageType × Channel). Safe to re-run."

    def add_arguments(self, parser):
        parser.add_argument(
            "--force",
            action="store_true",
            help="Overwrite the body/subject of existing templates.",
        )

    def handle(self, *args, **options):
        force = options["force"]
        created_count = 0
        updated_count = 0

        for data in DEFAULTS:
            obj, created = MessageTemplate.objects.get_or_create(
                message_type=data["message_type"],
                channel=data["channel"],
                defaults={
                    "subject":   data["subject"],
                    "body":      data["body"],
                    "is_active": True,
                },
            )
            if created:
                created_count += 1
                self.stdout.write(self.style.SUCCESS(f"  Created: {obj}"))
            elif force:
                obj.subject = data["subject"]
                obj.body    = data["body"]
                obj.save(update_fields=["subject", "body", "updated_at"])
                updated_count += 1
                self.stdout.write(self.style.WARNING(f"  Updated: {obj}"))
            else:
                self.stdout.write(f"  Skipped (exists): {obj}")

        self.stdout.write(
            self.style.SUCCESS(
                f"\nDone. Created: {created_count}, Updated: {updated_count}, "
                f"Skipped: {len(DEFAULTS) - created_count - updated_count}"
            )
        )
