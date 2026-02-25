"""
Django settings for recruitflow project.

Uses django-environ to load configuration from a .env file.
See .env.example for all required environment variables.
"""

from pathlib import Path

import environ

# ─── Paths ─────────────────────────────────────────────────────────────────────

BASE_DIR = Path(__file__).resolve().parent.parent

# ─── Environment ───────────────────────────────────────────────────────────────

env = environ.Env(
    DEBUG=(bool, False),
    ALLOWED_HOSTS=(list, ["localhost", "127.0.0.1"]),
)

environ.Env.read_env(BASE_DIR / ".env")

# ─── Core ──────────────────────────────────────────────────────────────────────

SECRET_KEY = env("SECRET_KEY")

PORT = env.int("PORT", default=8010)

DEBUG = env("DEBUG")

# Strip whitespace from each host to guard against accidental spaces in .env
ALLOWED_HOSTS = [h.strip() for h in env.list("ALLOWED_HOSTS")]

# Full origin URLs required by Django's CSRF check (must include scheme).
# ngrok terminates TLS, so its origin must use https://.
CSRF_TRUSTED_ORIGINS = [o.strip() for o in env.list("CSRF_TRUSTED_ORIGINS", default=[])]

# Trust the forwarded protocol header set by ngrok (and any reverse proxy).
# Without this, Django would treat all proxied requests as plain HTTP.
USE_X_FORWARDED_HOST = True
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")

# ─── Application Definition ────────────────────────────────────────────────────

DJANGO_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
]

THIRD_PARTY_APPS = [
    "django_apscheduler",
]

LOCAL_APPS = [
    "candidates",
    "positions",
    "applications",
    "calls",
    "evaluations",
    "messaging",
    "cvs",
    "webhooks",
    "prompts",
    "scheduler",
]

INSTALLED_APPS = DJANGO_APPS + THIRD_PARTY_APPS + LOCAL_APPS

# ─── Middleware ─────────────────────────────────────────────────────────────────

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "recruitflow.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

WSGI_APPLICATION = "recruitflow.wsgi.application"

# ─── Database ──────────────────────────────────────────────────────────────────
# Reads DATABASE_URL from .env, e.g.:
#   postgres://USER:PASSWORD@HOST:PORT/DBNAME

DATABASES = {
    "default": env.db("DATABASE_URL"),
}

# ─── Password Validation ───────────────────────────────────────────────────────

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

# ─── Internationalisation ──────────────────────────────────────────────────────

LANGUAGE_CODE = "en-us"

TIME_ZONE = "UTC"

USE_I18N = True

USE_TZ = True

# ─── Static & Media Files ──────────────────────────────────────────────────────

STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"

MEDIA_URL = "media/"
MEDIA_ROOT = BASE_DIR / env("MEDIA_ROOT", default="media")

# ─── Primary Key ───────────────────────────────────────────────────────────────

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# ─── Third-Party: APScheduler ──────────────────────────────────────────────────

APSCHEDULER_DATETIME_FORMAT = "N j, Y, f:s a"
APSCHEDULER_RUN_NOW_TIMEOUT = 25
APSCHEDULER_TIMEZONE = env("APSCHEDULER_TIMEZONE", default="UTC")

# ─── Third-Party: Anthropic ────────────────────────────────────────────────────

ANTHROPIC_API_KEY = env("ANTHROPIC_API_KEY", default="")
ANTHROPIC_MODEL = env("ANTHROPIC_MODEL", default="claude-sonnet-4-20250514")
ANTHROPIC_FAST_MODEL = env("ANTHROPIC_FAST_MODEL", default="claude-3-5-haiku-20241022")

# ─── Third-Party: ElevenLabs ───────────────────────────────────────────────────

ELEVENLABS_API_KEY = env("ELEVENLABS_API_KEY", default="")
ELEVENLABS_AGENT_ID = env("ELEVENLABS_AGENT_ID", default="")
ELEVENLABS_PHONE_NUMBER_ID = env("ELEVENLABS_PHONE_NUMBER_ID", default="")
ELEVENLABS_WEBHOOK_SECRET = env("ELEVENLABS_WEBHOOK_SECRET", default="")

# ─── Third-Party: Whapi ────────────────────────────────────────────────────────

WHAPI_TOKEN = env("WHAPI_TOKEN", default="")
WHAPI_API_URL = env("WHAPI_API_URL", default="")
WHAPI_WEBHOOK_SECRET = env("WHAPI_WEBHOOK_SECRET", default="")

# ─── Third-Party: Google / Gmail ───────────────────────────────────────────────

GOOGLE_CLIENT_ID = env("GOOGLE_CLIENT_ID", default="")
GOOGLE_CLIENT_SECRET = env("GOOGLE_CLIENT_SECRET", default="")
GOOGLE_REFRESH_TOKEN = env("GOOGLE_REFRESH_TOKEN", default="")

GMAIL_INBOX_LABEL = env("GMAIL_INBOX_LABEL", default="CVs")
GMAIL_PROCESSED_LABEL = env("GMAIL_PROCESSED_LABEL", default="CVs-Processed")
GMAIL_POLL_ENABLED = env.bool("GMAIL_POLL_ENABLED", default=True)
GMAIL_POLL_MINUTES = env.int("GMAIL_POLL_MINUTES", default=15)
