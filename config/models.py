"""
config/models.py

Lightweight DB-backed configuration models.

OAuthCredential — singleton row holding the connected Gmail OAuth tokens.
SystemSetting   — key/value store for runtime-configurable settings.
"""

from django.db import models
from django.utils import timezone


class OAuthCredential(models.Model):
    """
    Singleton row storing the connected Gmail OAuth tokens.
    There should only ever be one row; use .objects.first() to fetch it.
    """

    email_address = models.CharField(max_length=255)
    refresh_token = models.TextField()
    access_token = models.TextField(blank=True)
    token_expiry = models.DateTimeField(null=True, blank=True)
    connected_at = models.DateTimeField(default=timezone.now)

    class Meta:
        verbose_name = "Gmail OAuth Credential"
        verbose_name_plural = "Gmail OAuth Credentials"

    def __str__(self):
        return f"Gmail: {self.email_address}"


class SystemSetting(models.Model):
    """
    Key/value store for runtime-configurable settings.

    Known keys:
      gmail_poll_enabled  — "true" / "false"
      gmail_poll_minutes  — integer string (e.g. "15")
    """

    key = models.CharField(max_length=100, unique=True)
    value = models.TextField()
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "System Setting"
        verbose_name_plural = "System Settings"

    def __str__(self):
        return f"{self.key} = {self.value}"

    # ── Convenience helpers ───────────────────────────────────────────────────

    @classmethod
    def get(cls, key: str, default=None) -> str | None:
        try:
            return cls.objects.get(key=key).value
        except cls.DoesNotExist:
            return default

    @classmethod
    def set(cls, key: str, value) -> None:
        cls.objects.update_or_create(key=key, defaults={"value": str(value)})

    @classmethod
    def get_bool(cls, key: str, default: bool = True) -> bool:
        val = cls.get(key)
        if val is None:
            return default
        return val.strip().lower() in ("true", "1", "yes")

    @classmethod
    def get_int(cls, key: str, default: int | None = None) -> int | None:
        val = cls.get(key)
        if val is None:
            return default
        try:
            return int(val)
        except (ValueError, TypeError):
            return default
