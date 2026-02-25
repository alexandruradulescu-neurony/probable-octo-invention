"""
recruitflow/urls.py

Root URL configuration.
"""

from django.contrib import admin
from django.contrib.auth import views as auth_views
from django.urls import include, path

from recruitflow.views import DashboardView

urlpatterns = [
    # ── Admin ──────────────────────────────────────────────────────────────────
    path("admin/", admin.site.urls),

    # ── Authentication ─────────────────────────────────────────────────────────
    path(
        "login/",
        auth_views.LoginView.as_view(template_name="registration/login.html"),
        name="login",
    ),
    path("logout/", auth_views.LogoutView.as_view(), name="logout"),

    # ── Dashboard ──────────────────────────────────────────────────────────────
    path("", DashboardView.as_view(), name="dashboard"),

    # ── App routes ─────────────────────────────────────────────────────────────
    path("positions/", include("positions.urls", namespace="positions")),
    path("candidates/", include("candidates.urls", namespace="candidates")),
    path("applications/", include("applications.urls", namespace="applications")),
    path("cvs/", include("cvs.urls", namespace="cvs")),
    path("prompts/", include("prompts.urls", namespace="prompts")),

    # ── Webhooks (CSRF-exempt, no login required) ──────────────────────────────
    path("webhooks/", include("webhooks.urls", namespace="webhooks")),
]
