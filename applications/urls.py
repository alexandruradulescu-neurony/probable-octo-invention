"""
applications/urls.py
"""

from django.urls import path

from applications import views

app_name = "applications"

urlpatterns = [
    path("", views.ApplicationListView.as_view(), name="list"),
    path("<int:pk>/", views.ApplicationDetailView.as_view(), name="detail"),
]
