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

DEBUG = env("DEBUG")

ALLOWED_HOSTS = env("ALLOWED_HOSTS")

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

# ─── Static Files ──────────────────────────────────────────────────────────────

STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"

MEDIA_URL = "media/"
MEDIA_ROOT = BASE_DIR / "media"

# ─── Primary Key ───────────────────────────────────────────────────────────────

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# ─── Third-Party: APScheduler ──────────────────────────────────────────────────

APSCHEDULER_DATETIME_FORMAT = "N j, Y, f:s a"
APSCHEDULER_RUN_NOW_TIMEOUT = env.int("APSCHEDULER_RUN_NOW_TIMEOUT", default=25)

# ─── Third-Party: Anthropic ────────────────────────────────────────────────────

ANTHROPIC_API_KEY = env("ANTHROPIC_API_KEY", default="")

# ─── Third-Party: ElevenLabs ───────────────────────────────────────────────────

ELEVENLABS_API_KEY = env("ELEVENLABS_API_KEY", default="")
ELEVENLABS_VOICE_ID = env("ELEVENLABS_VOICE_ID", default="")

# ─── Third-Party: Whapi ────────────────────────────────────────────────────────

WHAPI_API_KEY = env("WHAPI_API_KEY", default="")
WHAPI_BASE_URL = env("WHAPI_BASE_URL", default="https://gate.whapi.cloud")

# ─── Third-Party: Gmail ────────────────────────────────────────────────────────

GMAIL_CLIENT_ID = env("GMAIL_CLIENT_ID", default="")
GMAIL_CLIENT_SECRET = env("GMAIL_CLIENT_SECRET", default="")
GMAIL_REDIRECT_URI = env("GMAIL_REDIRECT_URI", default="http://localhost:8000/oauth2callback")
GMAIL_CREDENTIALS_FILE = env("GMAIL_CREDENTIALS_FILE", default="credentials.json")
GMAIL_TOKEN_FILE = env("GMAIL_TOKEN_FILE", default="token.json")
