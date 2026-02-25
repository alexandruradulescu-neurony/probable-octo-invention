from django.db import models


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
