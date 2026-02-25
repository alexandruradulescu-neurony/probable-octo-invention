from django.db import models


class PromptTemplate(models.Model):
    """
    The meta-prompt — an instruction set sent to Claude when the recruiter
    clicks "Generate Prompts" on a Position. Tells Claude how to produce
    system_prompt, first_message, and qualification_prompt from position details.

    Stored as a global setting: only one template is active at a time.
    Old versions are retained for audit trail purposes.
    The version field is managed by the application layer on save.
    """

    name = models.CharField(max_length=255)
    # Only one template may be active at a time.
    # Enforced by application logic, not a DB constraint, to allow atomic swaps.
    is_active = models.BooleanField(default=False)

    # The full prompt sent to Claude. Supports placeholders:
    # {title}, {description}, {campaign_questions}
    meta_prompt = models.TextField()

    # Auto-incremented by the application layer on each save for audit trail
    version = models.PositiveIntegerField(default=1)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-version"]
        verbose_name = "Prompt Template"
        verbose_name_plural = "Prompt Templates"

    def __str__(self) -> str:
        active_marker = " ✓" if self.is_active else ""
        return f"{self.name} v{self.version}{active_marker}"
