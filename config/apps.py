"""
config/apps.py

AppConfig for the config app.
Records SERVER_START_TIME at startup for uptime calculation.
"""

from datetime import datetime, timezone

from django.apps import AppConfig

SERVER_START_TIME: datetime | None = None


class ConfigConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "config"
    verbose_name = "Configuration"

    def ready(self):
        global SERVER_START_TIME
        SERVER_START_TIME = datetime.now(timezone.utc)
