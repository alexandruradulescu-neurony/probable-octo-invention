from django.db import models


class Candidate(models.Model):
    class Source(models.TextChoices):
        META_FORM = "meta_form", "Meta Form"
        MANUAL = "manual", "Manual"

    # Name fields — full_name preserved from Meta CSV; first/last parsed on import
    first_name = models.CharField(max_length=150)
    last_name = models.CharField(max_length=150)
    full_name = models.CharField(max_length=300)

    # Contact
    phone = models.CharField(max_length=50, db_index=True)
    email = models.CharField(max_length=254, db_index=True)
    # Populated only when WhatsApp number differs from the main phone number
    whatsapp_number = models.CharField(max_length=50, null=True, blank=True)

    # Source & Meta lead data
    source = models.CharField(
        max_length=20,
        choices=Source.choices,
        default=Source.META_FORM,
    )
    # Primary dedup key for Meta CSV imports (e.g. "l:1990233898539318")
    meta_lead_id = models.CharField(max_length=100, unique=True, null=True, blank=True)
    meta_created_time = models.DateTimeField(null=True, blank=True)
    campaign_name = models.CharField(max_length=255, null=True, blank=True)
    # Platform identifier from Meta (e.g. "fb", "ig")
    platform = models.CharField(max_length=20, null=True, blank=True)

    # Campaign-specific question/answer pairs from the Meta lead form.
    # Keys = column headers (underscored), values = answer text.
    # Example: {"ai_permis_de_conducere_categoria_b": "da"}
    form_answers = models.JSONField(null=True, blank=True)

    notes = models.TextField(null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "Candidate"
        verbose_name_plural = "Candidates"

    def __str__(self) -> str:
        return f"{self.full_name} ({self.phone})"
