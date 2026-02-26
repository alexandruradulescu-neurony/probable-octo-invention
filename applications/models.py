from django.conf import settings
from django.db import models


class Application(models.Model):
    """
    Core pipeline entity. Links a Candidate to a Position and owns all
    workflow state. A candidate may have multiple Applications across
    different positions (many-to-many via this join table).
    """

    class Status(models.TextChoices):
        # ── Pre-call ─────────────────────────────────────────────────────────
        PENDING_CALL = "pending_call", "Pending Call"
        CALL_QUEUED = "call_queued", "Call Queued"

        # ── In-call ───────────────────────────────────────────────────────────
        CALL_IN_PROGRESS = "call_in_progress", "Call In Progress"
        CALL_COMPLETED = "call_completed", "Call Completed"
        CALL_FAILED = "call_failed", "Call Failed"

        # ── Post-call / scoring ───────────────────────────────────────────────
        SCORING = "scoring", "Scoring"

        # ── Qualified path ────────────────────────────────────────────────────
        QUALIFIED = "qualified", "Qualified"
        AWAITING_CV = "awaiting_cv", "Awaiting CV"
        CV_FOLLOWUP_1 = "cv_followup_1", "CV Follow-up 1"
        CV_FOLLOWUP_2 = "cv_followup_2", "CV Follow-up 2"
        CV_OVERDUE = "cv_overdue", "CV Overdue"
        CV_RECEIVED = "cv_received", "CV Received"

        # ── Not-qualified path ────────────────────────────────────────────────
        NOT_QUALIFIED = "not_qualified", "Not Qualified"
        AWAITING_CV_REJECTED = "awaiting_cv_rejected", "Awaiting CV – Not Qualified"
        CV_RECEIVED_REJECTED = "cv_received_rejected", "CV Received – Not Qualified"

        # ── Special outcomes ──────────────────────────────────────────────────
        CALLBACK_SCHEDULED = "callback_scheduled", "Callback Scheduled"
        NEEDS_HUMAN = "needs_human", "Needs Human"

        # ── Terminal ──────────────────────────────────────────────────────────
        CLOSED = "closed", "Closed"

    candidate = models.ForeignKey(
        "candidates.Candidate",
        on_delete=models.CASCADE,
        related_name="applications",
    )
    position = models.ForeignKey(
        "positions.Position",
        on_delete=models.CASCADE,
        related_name="applications",
    )
    status = models.CharField(
        max_length=30,
        choices=Status.choices,
        default=Status.PENDING_CALL,
        db_index=True,
    )

    # Scoring results (null until Claude evaluation completes)
    qualified = models.BooleanField(null=True, blank=True, db_index=True)
    score = models.PositiveSmallIntegerField(null=True, blank=True)
    score_notes = models.TextField(null=True, blank=True)

    # CV & scheduling timestamps
    cv_received_at = models.DateTimeField(null=True, blank=True)
    callback_scheduled_at = models.DateTimeField(null=True, blank=True, db_index=True)

    # Populated when Claude detects a human-escalation scenario
    needs_human_reason = models.TextField(null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "Application"
        verbose_name_plural = "Applications"
        # A candidate may only hold one active application per position
        unique_together = [("candidate", "position")]

    def __str__(self) -> str:
        return f"{self.candidate} → {self.position} [{self.status}]"

    def change_status(self, new_status: str, changed_by=None, note: str = ""):
        """
        Transition status and create an audit StatusChange record.
        Call this instead of setting status + save() directly when you
        want an audited transition.
        """
        old_status = self.status
        if old_status == new_status:
            return
        self.status = new_status
        self.save(update_fields=["status", "updated_at"])
        StatusChange.objects.create(
            application=self,
            from_status=old_status,
            to_status=new_status,
            changed_by=changed_by,
            note=note,
        )
        from django.core.cache import cache
        cache.delete("sidebar_counts")


class StatusChange(models.Model):
    """
    Audit trail entry recording every Application status transition.
    """

    application = models.ForeignKey(
        Application,
        on_delete=models.CASCADE,
        related_name="status_changes",
    )
    from_status = models.CharField(max_length=30, choices=Application.Status.choices)
    to_status = models.CharField(max_length=30, choices=Application.Status.choices)
    changed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="status_changes",
    )
    note = models.TextField(blank=True, default="")
    changed_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-changed_at"]
        verbose_name = "Status Change"
        verbose_name_plural = "Status Changes"

    def __str__(self) -> str:
        return f"App#{self.application_id}: {self.from_status} → {self.to_status}"
