"""
positions/urls.py
"""

from django.urls import path

from positions import views

app_name = "positions"

urlpatterns = [
    path("", views.PositionListView.as_view(), name="list"),
    path("create/", views.PositionCreateView.as_view(), name="create"),
    path("<int:pk>/edit/", views.PositionUpdateView.as_view(), name="edit"),
]
