"""
config/urls.py

URL patterns for the Settings section.
"""

from django.urls import path

from config import views

app_name = "config"

urlpatterns = [
    path("", views.settings_view, name="settings"),
    path("gmail/authorize/", views.gmail_authorize, name="gmail_authorize"),
    path("gmail/callback/", views.gmail_callback, name="gmail_callback"),
    path("gmail/disconnect/", views.gmail_disconnect, name="gmail_disconnect"),
    path("polling/toggle/", views.toggle_polling, name="toggle_polling"),
    path("polling/interval/", views.update_interval, name="update_interval"),
    path("status.json", views.status_json, name="status_json"),
]
