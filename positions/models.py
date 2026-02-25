from django.db import models


class Position(models.Model):
    class Status(models.TextChoices):
        OPEN = "open", "Open"
        PAUSED = "paused", "Paused"
        CLOSED = "closed", "Closed"

    title = models.CharField(max_length=255)
    description = models.TextField()
    status = models.CharField(
        max_length=10,
        choices=Status.choices,
        default=Status.OPEN,
    )

    # Screening questions for this role, one per line.
    # Used as input to auto-generate all three prompt fields below.
    campaign_questions = models.TextField()

    # Injected into ElevenLabs dynamically per call.
    # "Allow Overrides" must be enabled in the ElevenLabs agent's Security settings.
    system_prompt = models.TextField(null=True, blank=True)
    first_message = models.TextField(null=True, blank=True)

    # Sent to Claude along with the call transcript for scoring.
    qualification_prompt = models.TextField(null=True, blank=True)

    # Call scheduling & retry config
    call_retry_max = models.PositiveSmallIntegerField(
        default=2,
        help_text="Max call attempts before marking the application call_failed.",
    )
    call_retry_interval_minutes = models.PositiveIntegerField(
        default=60,
        help_text="Minutes to wait between retry attempts.",
    )
    calling_hour_start = models.PositiveSmallIntegerField(
        default=10,
        help_text="Earliest hour to place calls (24-hour format).",
    )
    calling_hour_end = models.PositiveSmallIntegerField(
        default=18,
        help_text="Latest hour to place calls (24-hour format).",
    )

    # CV follow-up config (qualified candidates only)
    follow_up_interval_hours = models.PositiveIntegerField(
        default=24,
        help_text="Hours between CV follow-up messages for qualified candidates.",
    )
    rejected_cv_timeout_days = models.PositiveSmallIntegerField(
        default=7,
        help_text="Days to wait for a rejected candidate's CV before closing the application.",
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "Position"
        verbose_name_plural = "Positions"

    def __str__(self) -> str:
        return f"[{self.status}] {self.title}"
