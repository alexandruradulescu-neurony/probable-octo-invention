from django.db import models
from django.utils import timezone


class LLMEvaluation(models.Model):
    """
    Claude's scoring result for a completed call transcript.
    Receives transcript + position qualification_prompt + candidate form_answers.
    Detects four possible outcomes including special-case escalations.
    """

    class Outcome(models.TextChoices):
        QUALIFIED = "qualified", "Qualified"
        NOT_QUALIFIED = "not_qualified", "Not Qualified"
        CALLBACK_REQUESTED = "callback_requested", "Callback Requested"
        NEEDS_HUMAN = "needs_human", "Needs Human"

    application = models.ForeignKey(
        "applications.Application",
        on_delete=models.CASCADE,
        related_name="evaluations",
    )
    call = models.ForeignKey(
        "calls.Call",
        on_delete=models.CASCADE,
        related_name="evaluations",
    )

    outcome = models.CharField(max_length=20, choices=Outcome.choices)
    qualified = models.BooleanField()
    score = models.PositiveSmallIntegerField(help_text="0–100")
    reasoning = models.TextField()

    # Callback detection
    callback_requested = models.BooleanField(default=False)
    callback_notes = models.TextField(null=True, blank=True)

    # Human-escalation detection
    needs_human = models.BooleanField(default=False)
    needs_human_notes = models.TextField(null=True, blank=True)

    # Full Claude API response stored for debugging / re-evaluation
    raw_response = models.JSONField()

    evaluated_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ["-evaluated_at"]
        verbose_name = "LLM Evaluation"
        verbose_name_plural = "LLM Evaluations"

    def __str__(self) -> str:
        return f"Eval: {self.outcome} (score {self.score}) — {self.application}"
