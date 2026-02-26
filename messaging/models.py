from django.db import models


class MessageTemplate(models.Model):
    """
    Editable body templates for every outbound message type × channel
    combination. Used by messaging.services as the primary source of
    message text; falls back to hardcoded defaults if no active template
    is found for a given combination.

    Available body placeholders:
      {first_name}       — candidate's first name
      {position_title}   — position title
      {application_pk}   — application reference number (integer)
    """

    class MessageType(models.TextChoices):
        CV_REQUEST          = "cv_request",          "CV Request (Qualified)"
        CV_REQUEST_REJECTED = "cv_request_rejected",  "CV Request (Not Qualified)"
        CV_FOLLOWUP_1       = "cv_followup_1",        "CV Follow-up 1"
        CV_FOLLOWUP_2       = "cv_followup_2",        "CV Follow-up 2"
        REJECTION           = "rejection",            "Rejection"

    class Channel(models.TextChoices):
        EMAIL    = "email",    "Email"
        WHATSAPP = "whatsapp", "WhatsApp"

    message_type = models.CharField(max_length=30, choices=MessageType.choices, db_index=True)
    channel      = models.CharField(max_length=10, choices=Channel.choices,     db_index=True)

    # Email-only subject line; ignored for WhatsApp.
    subject = models.CharField(max_length=255, blank=True)
    body    = models.TextField()

    is_active  = models.BooleanField(default=True, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = [("message_type", "channel")]
        ordering        = ["message_type", "channel"]
        verbose_name    = "Message Template"
        verbose_name_plural = "Message Templates"

    def __str__(self) -> str:
        return f"{self.get_message_type_display()} / {self.get_channel_display()}"

    # ── Placeholder resolution ────────────────────────────────────────────────

    PLACEHOLDER_DOCS = (
        "{first_name}",
        "{position_title}",
        "{application_pk}",
    )

    def render(self, *, first_name: str = "", position_title: str = "", application_pk: int | str = "") -> str:
        """Return body with all placeholders substituted."""
        return (
            self.body
            .replace("{first_name}", str(first_name))
            .replace("{position_title}", str(position_title))
            .replace("{application_pk}", str(application_pk))
        )

    def render_subject(self, *, position_title: str = "") -> str:
        """Return subject with all placeholders substituted."""
        return self.subject.replace("{position_title}", str(position_title))


class CandidateReply(models.Model):
    """
    An inbound message received from a candidate via WhatsApp or email.

    Decoupled from the outbound Message model because inbound messages do not
    have a message_type, a delivery status, or a mandatory application FK.
    """

    class Channel(models.TextChoices):
        EMAIL    = "email",    "Email"
        WHATSAPP = "whatsapp", "WhatsApp"

    candidate   = models.ForeignKey(
        "candidates.Candidate",
        null=True, blank=True,
        on_delete=models.SET_NULL,
        related_name="replies",
    )
    application = models.ForeignKey(
        "applications.Application",
        null=True, blank=True,
        on_delete=models.SET_NULL,
        related_name="candidate_replies",
    )
    channel     = models.CharField(max_length=10, choices=Channel.choices, db_index=True)
    sender      = models.CharField(max_length=255)   # raw phone or email address
    subject     = models.CharField(max_length=500, blank=True)  # email only
    body        = models.TextField()
    received_at = models.DateTimeField(auto_now_add=True, db_index=True)
    is_read     = models.BooleanField(default=False, db_index=True)
    external_id = models.CharField(max_length=255, null=True, blank=True)

    class Meta:
        ordering = ["-received_at"]
        verbose_name = "Candidate Reply"
        verbose_name_plural = "Candidate Replies"

    def __str__(self) -> str:
        candidate_str = self.candidate.full_name if self.candidate else self.sender
        return f"{self.channel} reply from {candidate_str}"


class Message(models.Model):
    """
    Every outbound communication sent to a candidate. Full audit trail
    of all email and WhatsApp messages across the pipeline.
    """

    class Channel(models.TextChoices):
        EMAIL = "email", "Email"
        WHATSAPP = "whatsapp", "WhatsApp"

    class MessageType(models.TextChoices):
        CV_REQUEST = "cv_request", "CV Request"
        CV_REQUEST_REJECTED = "cv_request_rejected", "CV Request (Rejected)"
        CV_FOLLOWUP_1 = "cv_followup_1", "CV Follow-up 1"
        CV_FOLLOWUP_2 = "cv_followup_2", "CV Follow-up 2"
        REJECTION = "rejection", "Rejection"
        OTHER = "other", "Other"

    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        SENT = "sent", "Sent"
        DELIVERED = "delivered", "Delivered"
        FAILED = "failed", "Failed"

    application = models.ForeignKey(
        "applications.Application",
        on_delete=models.CASCADE,
        related_name="messages",
    )
    channel = models.CharField(max_length=10, choices=Channel.choices)
    message_type = models.CharField(max_length=30, choices=MessageType.choices)
    status = models.CharField(
        max_length=10,
        choices=Status.choices,
        default=Status.PENDING,
        db_index=True,
    )

    # External message ID returned by Gmail API or Whapi
    external_id = models.CharField(max_length=255, null=True, blank=True)

    body = models.TextField()
    sent_at = models.DateTimeField(null=True, blank=True)
    error_detail = models.CharField(max_length=500, null=True, blank=True)

    class Meta:
        ordering = ["-sent_at", "-id"]
        verbose_name = "Message"
        verbose_name_plural = "Messages"

    def __str__(self) -> str:
        return f"{self.channel}/{self.message_type} [{self.status}] — {self.application}"
