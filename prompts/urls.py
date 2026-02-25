"""
prompts/urls.py
"""

from django.urls import path

from prompts import views

app_name = "prompts"

urlpatterns = [
    path("", views.PromptTemplateListView.as_view(), name="list"),
    path("create/", views.PromptTemplateCreateView.as_view(), name="create"),
    path("<int:pk>/edit/", views.PromptTemplateUpdateView.as_view(), name="edit"),
    path("<int:pk>/toggle-active/", views.ToggleActiveView.as_view(), name="toggle_active"),
    path("<int:pk>/test-generate/", views.TestGenerateView.as_view(), name="test_generate"),
]
