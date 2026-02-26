"""
config/views.py

Settings page: Gmail OAuth flow, polling controls, service status API.
"""

import json
import logging
import os
import time
from datetime import datetime, timezone

import requests as http_requests
from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.shortcuts import redirect, render
from django.views.decorators.http import require_POST

from config.models import OAuthCredential, SystemSetting

logger = logging.getLogger(__name__)

GMAIL_SCOPES = ["https://mail.google.com/"]


# ─────────────────────────────────────────────────────────────────────────────
# Main settings page
# ─────────────────────────────────────────────────────────────────────────────


@login_required
def settings_view(request):
    cred = OAuthCredential.objects.first()
    poll_enabled = SystemSetting.get_bool("gmail_poll_enabled", default=settings.GMAIL_POLL_ENABLED)
    poll_minutes = SystemSetting.get_int("gmail_poll_minutes", default=settings.GMAIL_POLL_MINUTES)

    google_configured = bool(settings.GOOGLE_CLIENT_ID and settings.GOOGLE_CLIENT_SECRET)
    redirect_uri = getattr(settings, "GOOGLE_REDIRECT_URI", "")

    context = {
        "cred": cred,
        "poll_enabled": poll_enabled,
        "poll_minutes": poll_minutes,
        "google_configured": google_configured,
        "redirect_uri": redirect_uri,
        "anthropic_key_set": bool(settings.ANTHROPIC_API_KEY),
        "anthropic_model": settings.ANTHROPIC_MODEL,
        "whapi_token_set": bool(settings.WHAPI_TOKEN),
        "whapi_url": settings.WHAPI_API_URL,
    }
    return render(request, "config/settings.html", context)


# ─────────────────────────────────────────────────────────────────────────────
# Gmail OAuth flow
# ─────────────────────────────────────────────────────────────────────────────


@login_required
def gmail_authorize(request):
    """Build Google consent URL and redirect the user to it."""
    client_id = settings.GOOGLE_CLIENT_ID
    client_secret = settings.GOOGLE_CLIENT_SECRET
    redirect_uri = getattr(settings, "GOOGLE_REDIRECT_URI", "")

    if not client_id or not client_secret:
        messages.error(
            request,
            "GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET must be set in .env before connecting.",
        )
        return redirect("config:settings")

    if not redirect_uri:
        messages.error(
            request,
            "GOOGLE_REDIRECT_URI is not set in .env. "
            "Add it (e.g. http://localhost:8010/settings/gmail/callback/) and restart.",
        )
        return redirect("config:settings")

    if settings.DEBUG:
        os.environ.setdefault("OAUTHLIB_INSECURE_TRANSPORT", "1")

    from google_auth_oauthlib.flow import Flow

    flow = Flow.from_client_config(
        {
            "web": {
                "client_id": client_id,
                "client_secret": client_secret,
                "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                "token_uri": "https://oauth2.googleapis.com/token",
            }
        },
        scopes=GMAIL_SCOPES,
        redirect_uri=redirect_uri,
    )

    auth_url, state = flow.authorization_url(
        access_type="offline",
        include_granted_scopes="true",
        prompt="consent",
    )

    request.session["google_oauth_state"] = state
    return redirect(auth_url)


@login_required
def gmail_callback(request):
    """Receive OAuth callback, exchange code for tokens, persist to DB."""
    error = request.GET.get("error")
    if error:
        messages.error(request, f"Google OAuth error: {error}")
        return redirect("config:settings")

    code = request.GET.get("code")
    state = request.session.get("google_oauth_state")

    if not code:
        messages.error(request, "No authorization code received from Google.")
        return redirect("config:settings")

    client_id = settings.GOOGLE_CLIENT_ID
    client_secret = settings.GOOGLE_CLIENT_SECRET
    redirect_uri = getattr(settings, "GOOGLE_REDIRECT_URI", "")

    if settings.DEBUG:
        os.environ.setdefault("OAUTHLIB_INSECURE_TRANSPORT", "1")

    try:
        from google_auth_oauthlib.flow import Flow
        from googleapiclient.discovery import build

        flow = Flow.from_client_config(
            {
                "web": {
                    "client_id": client_id,
                    "client_secret": client_secret,
                    "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                    "token_uri": "https://oauth2.googleapis.com/token",
                }
            },
            scopes=GMAIL_SCOPES,
            redirect_uri=redirect_uri,
            state=state,
        )

        flow.fetch_token(code=code)
        creds = flow.credentials

        svc = build("gmail", "v1", credentials=creds, cache_discovery=False)
        profile = svc.users().getProfile(userId="me").execute()
        email = profile.get("emailAddress", "unknown@gmail.com")

        OAuthCredential.objects.all().delete()
        OAuthCredential.objects.create(
            email_address=email,
            refresh_token=creds.refresh_token or "",
            access_token=creds.token or "",
            token_expiry=creds.expiry,
        )

        request.session.pop("google_oauth_state", None)
        messages.success(request, f"Gmail connected successfully: {email}")
        logger.info("Gmail OAuth connected for %s", email)

    except Exception as exc:
        logger.error("Gmail OAuth callback failed: %s", exc, exc_info=True)
        messages.error(request, f"Gmail connection failed: {exc}")

    return redirect("config:settings")


@login_required
@require_POST
def gmail_disconnect(request):
    """Delete the stored OAuth credential, disconnecting Gmail."""
    deleted, _ = OAuthCredential.objects.all().delete()
    if deleted:
        messages.success(request, "Gmail disconnected.")
    else:
        messages.warning(request, "No Gmail connection found.")
    return redirect("config:settings")


# ─────────────────────────────────────────────────────────────────────────────
# Polling controls
# ─────────────────────────────────────────────────────────────────────────────


@login_required
@require_POST
def toggle_polling(request):
    """Flip the gmail_poll_enabled system setting."""
    current = SystemSetting.get_bool("gmail_poll_enabled", default=settings.GMAIL_POLL_ENABLED)
    SystemSetting.set("gmail_poll_enabled", not current)
    state = "enabled" if not current else "disabled"
    messages.success(request, f"Gmail auto-polling {state}.")
    return redirect("config:settings")


@login_required
@require_POST
def update_interval(request):
    """Update gmail_poll_minutes from form POST."""
    raw = request.POST.get("poll_minutes", "")
    try:
        minutes = int(raw)
        if minutes < 1 or minutes > 1440:
            raise ValueError("Out of range")
        SystemSetting.set("gmail_poll_minutes", minutes)
        messages.success(request, f"Polling interval updated to {minutes} minute(s).")
    except (ValueError, TypeError):
        messages.error(request, "Invalid interval — must be a whole number between 1 and 1440.")
    return redirect("config:settings")


# ─────────────────────────────────────────────────────────────────────────────
# Live status API
# ─────────────────────────────────────────────────────────────────────────────


@login_required
def status_json(request):
    """Return a JSON snapshot of all service statuses + system uptime."""
    return JsonResponse(
        {
            "gmail": _check_gmail(),
            "claude": _check_claude(),
            "whapi": _check_whapi(),
            "system": _check_system(),
        }
    )


# ── Private checkers ──────────────────────────────────────────────────────────


def _check_gmail() -> dict:
    cred = OAuthCredential.objects.first()
    if not cred:
        return {"status": "disconnected", "detail": "No OAuth credential stored.", "email": None}

    try:
        from google.auth.transport.requests import Request
        from google.oauth2.credentials import Credentials
        from googleapiclient.discovery import build

        goog_creds = Credentials(
            token=cred.access_token or None,
            refresh_token=cred.refresh_token,
            token_uri="https://oauth2.googleapis.com/token",
            client_id=settings.GOOGLE_CLIENT_ID,
            client_secret=settings.GOOGLE_CLIENT_SECRET,
            scopes=GMAIL_SCOPES,
        )
        if not goog_creds.valid:
            goog_creds.refresh(Request())
            cred.access_token = goog_creds.token
            cred.token_expiry = goog_creds.expiry
            cred.save(update_fields=["access_token", "token_expiry"])

        svc = build("gmail", "v1", credentials=goog_creds, cache_discovery=False)
        profile = svc.users().getProfile(userId="me").execute()
        return {
            "status": "ok",
            "email": profile.get("emailAddress"),
            "messages_total": profile.get("messagesTotal"),
        }
    except Exception as exc:
        return {"status": "error", "detail": str(exc), "email": cred.email_address}


def _check_claude() -> dict:
    api_key = settings.ANTHROPIC_API_KEY
    if not api_key:
        return {"status": "disconnected", "detail": "ANTHROPIC_API_KEY not set."}

    t0 = time.monotonic()
    try:
        import anthropic

        client = anthropic.Anthropic(api_key=api_key)
        client.messages.create(
            model=settings.ANTHROPIC_FAST_MODEL,
            max_tokens=1,
            messages=[{"role": "user", "content": "ping"}],
        )
        latency_ms = round((time.monotonic() - t0) * 1000)
        return {
            "status": "ok",
            "model": settings.ANTHROPIC_MODEL,
            "fast_model": settings.ANTHROPIC_FAST_MODEL,
            "latency_ms": latency_ms,
        }
    except Exception as exc:
        return {"status": "error", "detail": str(exc)}


def _check_whapi() -> dict:
    token = settings.WHAPI_TOKEN
    base_url = (settings.WHAPI_API_URL or "").rstrip("/")

    if not token or not base_url:
        return {"status": "disconnected", "detail": "WHAPI_TOKEN or WHAPI_API_URL not set."}

    t0 = time.monotonic()
    try:
        resp = http_requests.get(
            f"{base_url}/health",
            headers={"Authorization": f"Bearer {token}"},
            timeout=8,
        )
        latency_ms = round((time.monotonic() - t0) * 1000)
        if resp.status_code < 400:
            data = resp.json() if resp.content else {}
            return {
                "status": "ok",
                "latency_ms": latency_ms,
                "api_url": base_url,
                "token_prefix": token[:6] + "…",
                "detail": data,
            }
        return {
            "status": "error",
            "detail": f"HTTP {resp.status_code}: {resp.text[:200]}",
            "latency_ms": latency_ms,
        }
    except Exception as exc:
        return {"status": "error", "detail": str(exc)}


def _check_system() -> dict:
    from config import apps as config_apps

    start = config_apps.SERVER_START_TIME
    now = datetime.now(timezone.utc)
    uptime_seconds = int((now - start).total_seconds()) if start else None

    # Scheduler jobs info
    jobs_info = []
    try:
        from django_apscheduler.models import DjangoJob

        for job in DjangoJob.objects.all().order_by("id"):
            last_exec = (
                job.djangojobexecution_set.order_by("-run_time").first()
                if hasattr(job, "djangojobexecution_set")
                else None
            )
            jobs_info.append(
                {
                    "id": job.id,
                    "next_run_time": job.next_run_time.isoformat() if job.next_run_time else None,
                    "last_run_time": last_exec.run_time.isoformat() if last_exec else None,
                    "last_status": last_exec.status if last_exec else None,
                }
            )
    except Exception as exc:
        logger.warning("Could not fetch scheduler jobs: %s", exc)

    return {
        "status": "ok",
        "uptime_seconds": uptime_seconds,
        "server_start": start.isoformat() if start else None,
        "jobs": jobs_info,
    }
