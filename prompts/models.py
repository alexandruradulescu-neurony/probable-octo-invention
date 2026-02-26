from django.db import models


class PromptTemplate(models.Model):
    """
    A meta-prompt for a single prompt section, sent to Claude when generating
    prompts for a Position.

    One template per section can be active at a time; enforced by application
    logic in ToggleActiveView (not a DB constraint, to allow atomic swaps).
    Old versions are retained for audit trail purposes.
    """

    class Section(models.TextChoices):
        SYSTEM_PROMPT        = "system_prompt",        "System Prompt"
        FIRST_MESSAGE        = "first_message",        "First Message"
        QUALIFICATION_PROMPT = "qualification_prompt", "Qualification Prompt"

    # Which prompt section this template generates.
    # Nullable to preserve legacy records created before this field was added.
    section = models.CharField(
        max_length=30,
        choices=Section.choices,
        null=True,
        blank=True,
        db_index=True,
    )

    name = models.CharField(max_length=255)

    # Only one template per section may be active at a time.
    is_active = models.BooleanField(default=False, db_index=True)

    # The instruction sent to Claude for this section. Supports placeholders:
    # {title}, {description}, {campaign_questions}
    meta_prompt = models.TextField()

    # Auto-incremented by the application layer on each save for audit trail.
    version = models.PositiveIntegerField(default=1)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["section", "-version"]
        verbose_name = "Prompt Template"
        verbose_name_plural = "Prompt Templates"

    def __str__(self) -> str:
        section_label = self.get_section_display() if self.section else "—"
        active_marker = " ✓" if self.is_active else ""
        return f"[{section_label}] {self.name} v{self.version}{active_marker}"
