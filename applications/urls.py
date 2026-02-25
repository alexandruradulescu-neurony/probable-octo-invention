"""
applications/urls.py
"""

from django.urls import path

from applications import views

app_name = "applications"

urlpatterns = [
    path("", views.ApplicationListView.as_view(), name="list"),
    path("trigger-calls/", views.TriggerCallsView.as_view(), name="trigger_calls"),
    path("<int:pk>/", views.ApplicationDetailView.as_view(), name="detail"),
    path("<int:pk>/override-status/", views.StatusOverrideView.as_view(), name="override_status"),
    path("<int:pk>/add-note/", views.AddNoteView.as_view(), name="add_note"),
    path("<int:pk>/schedule-callback/", views.ScheduleCallbackView.as_view(), name="schedule_callback"),
    path("<int:pk>/trigger-followup/", views.TriggerFollowupView.as_view(), name="trigger_followup"),
    path("<int:pk>/upload-cv/", views.ManualCVUploadView.as_view(), name="upload_cv"),
]
