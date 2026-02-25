"""
candidates/urls.py

URL patterns for the candidates app (Spec § 12.4).
"""

from django.urls import path

from candidates import views

app_name = "candidates"

urlpatterns = [
    path("", views.CandidateListView.as_view(), name="list"),
    path("<int:pk>/", views.CandidateDetailView.as_view(), name="detail"),
    path("<int:pk>/notes/", views.CandidateUpdateNotesView.as_view(), name="update_notes"),
    path("<int:pk>/contact/", views.CandidateUpdateContactView.as_view(), name="update_contact"),
    path("import/", views.CSVImportView.as_view(), name="csv_import"),
]
