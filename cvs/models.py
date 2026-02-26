from django.db import models
from django.utils import timezone


class CVUpload(models.Model):
    """
    Received CV files.

    A CV belongs primarily to a Candidate (the person) — stored on the
    candidate FK so it can be retrieved directly from the candidate profile.
    It is also linked to one or more Applications so that each open
    application for that candidate gets its status advanced.

    A single inbound submission may produce multiple CVUpload rows (one per
    awaiting application) but they all share the same file_path on disk.
    """

    class Source(models.TextChoices):
        EMAIL_ATTACHMENT = "email_attachment", "Email Attachment"
        WHATSAPP_MEDIA = "whatsapp_media", "WhatsApp Media"
        MANUAL_UPLOAD = "manual_upload", "Manual Upload"

    class MatchMethod(models.TextChoices):
        EXACT_EMAIL = "exact_email", "Exact Email"
        EXACT_PHONE = "exact_phone", "Exact Phone"
        SUBJECT_ID = "subject_id", "Subject ID"
        FUZZY_NAME = "fuzzy_name", "Fuzzy Name"
        CV_CONTENT = "cv_content", "CV Content Extraction"
        MANUAL = "manual", "Manual Assignment"

    # Primary owner — the person who submitted the CV
    candidate = models.ForeignKey(
        "candidates.Candidate",
        on_delete=models.CASCADE,
        related_name="cv_uploads",
        null=True,
        blank=True,
    )
    # The specific application this upload advances (one record per application)
    application = models.ForeignKey(
        "applications.Application",
        on_delete=models.CASCADE,
        related_name="cv_uploads",
    )
    file_name = models.CharField(max_length=255)
    # Local filesystem path or S3 object key, depending on storage backend
    file_path = models.CharField(max_length=500, blank=True, default="")

    source = models.CharField(max_length=20, choices=Source.choices)

    # Null for manually uploaded CVs where matching is not applicable
    match_method = models.CharField(
        max_length=20,
        choices=MatchMethod.choices,
        null=True,
        blank=True,
    )

    # True when matched via medium-confidence method (fuzzy_name or cv_content).
    # Flagged for recruiter review in the CV Inbox screen.
    needs_review = models.BooleanField(default=False, db_index=True)

    received_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ["-received_at"]
        verbose_name = "CV Upload"
        verbose_name_plural = "CV Uploads"

    def __str__(self) -> str:
        return f"CV: {self.file_name} ({self.source})"


class UnmatchedInbound(models.Model):
    """
    Inbound messages (email or WhatsApp) with attachments that could not be
    automatically matched to any candidate. Held for manual recruiter review
    and assignment via the CV Inbox screen.
    """

    class Channel(models.TextChoices):
        EMAIL = "email", "Email"
        WHATSAPP = "whatsapp", "WhatsApp"

    channel = models.CharField(max_length=10, choices=Channel.choices)
    # Email address or WhatsApp phone number of the sender
    sender = models.CharField(max_length=255)

    # Email-specific fields (null for WhatsApp inbounds)
    subject = models.CharField(max_length=500, null=True, blank=True)
    body_snippet = models.TextField(null=True, blank=True)
    attachment_name = models.CharField(max_length=255, null=True, blank=True)

    # Full raw payload from the inbound source for debugging / re-processing
    raw_payload = models.JSONField()

    # CV file saved to disk at ingestion time so manual assignment has the file
    file_path = models.CharField(max_length=500, blank=True, default="")

    received_at = models.DateTimeField(default=timezone.now)

    # Resolution tracking
    resolved = models.BooleanField(default=False, db_index=True)
    resolved_by_application = models.ForeignKey(
        "applications.Application",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="resolved_unmatched_inbounds",
    )
    resolved_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-received_at"]
        verbose_name = "Unmatched Inbound"
        verbose_name_plural = "Unmatched Inbounds"

    def __str__(self) -> str:
        return f"Unmatched {self.channel} from {self.sender} ({self.received_at:%Y-%m-%d})"
