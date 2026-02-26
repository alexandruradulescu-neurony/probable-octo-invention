"""
config/migrations/0001_initial.py

Initial migration: OAuthCredential and SystemSetting tables.
"""

import django.utils.timezone
from django.db import migrations, models


class Migration(migrations.Migration):

    initial = True

    dependencies = []

    operations = [
        migrations.CreateModel(
            name="OAuthCredential",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("email_address", models.CharField(max_length=255)),
                ("refresh_token", models.TextField()),
                ("access_token", models.TextField(blank=True)),
                ("token_expiry", models.DateTimeField(blank=True, null=True)),
                ("connected_at", models.DateTimeField(default=django.utils.timezone.now)),
            ],
            options={
                "verbose_name": "Gmail OAuth Credential",
                "verbose_name_plural": "Gmail OAuth Credentials",
            },
        ),
        migrations.CreateModel(
            name="SystemSetting",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("key", models.CharField(max_length=100, unique=True)),
                ("value", models.TextField()),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
            options={
                "verbose_name": "System Setting",
                "verbose_name_plural": "System Settings",
            },
        ),
    ]
