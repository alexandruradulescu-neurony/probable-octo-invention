"""
cvs/urls.py
"""

from django.urls import path

from cvs import views

app_name = "cvs"

urlpatterns = [
    path("inbox/", views.CVInboxView.as_view(), name="inbox"),
    path("assign-unmatched/", views.AssignUnmatchedView.as_view(), name="assign_unmatched"),
    path("confirm-review/", views.ConfirmCVReviewView.as_view(), name="confirm_review"),
    path("reassign/", views.ReassignCVView.as_view(), name="reassign"),
    path("application-search/", views.ApplicationSearchView.as_view(), name="application_search"),
    path("<int:pk>/delete/", views.CVDeleteView.as_view(), name="cv_delete"),
]
