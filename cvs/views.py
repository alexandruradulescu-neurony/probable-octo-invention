"""
cvs/views.py

CV Inbox — Unmatched & Review Items.
Spec § 12.6.

Two tabs:
  Unmatched    : inbound items that couldn't be auto-matched.
                 POST action: "Assign to Application" → creates CVUpload + advances status.
  Needs Review : CVs auto-assigned via medium-confidence matching.
                 POST actions: "Confirm" (removes flag) or "Reassign" (move to another app).
"""

import logging

from django.contrib.auth.mixins import LoginRequiredMixin
from django.db import transaction
from django.http import HttpResponseBadRequest
from django.shortcuts import get_object_or_404, redirect
from django.utils import timezone
from django.views import View
from django.views.generic import TemplateView

from applications.models import Application
from cvs.helpers import advance_application_status, channel_to_source
from cvs.models import CVUpload, UnmatchedInbound

logger = logging.getLogger(__name__)

class CVInboxView(LoginRequiredMixin, TemplateView):
    """
    Main CV inbox page with two context lists.
    """
    template_name = "cvs/cv_inbox.html"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)

        ctx["unmatched_items"] = (
            UnmatchedInbound.objects
            .filter(resolved=False)
            .order_by("-received_at")
        )

        ctx["needs_review_items"] = (
            CVUpload.objects
            .filter(needs_review=True)
            .select_related("application__candidate", "application__position")
            .order_by("-received_at")
        )

        return ctx


class AssignUnmatchedView(LoginRequiredMixin, View):
    """
    POST handler: manually assign an UnmatchedInbound item to an Application.

    Creates a CVUpload, advances the Application status, and marks the
    UnmatchedInbound as resolved.

    POST params:
        unmatched_id    : int  (UnmatchedInbound PK)
        application_id  : int  (Application PK)
    """

    def post(self, request):
        unmatched_id = request.POST.get("unmatched_id")
        application_id = request.POST.get("application_id")

        if not unmatched_id or not application_id:
            return HttpResponseBadRequest("Missing unmatched_id or application_id.")

        unmatched = get_object_or_404(UnmatchedInbound, pk=unmatched_id, resolved=False)
        application = get_object_or_404(Application, pk=application_id)

        now = timezone.now()

        with transaction.atomic():
            CVUpload.objects.create(
                application=application,
                file_name=unmatched.attachment_name or "unknown",
                file_path="",
                source=channel_to_source(unmatched.channel),
                match_method=CVUpload.MatchMethod.MANUAL,
                needs_review=False,
            )

            advance_application_status(application)

            unmatched.resolved = True
            unmatched.resolved_by_application = application
            unmatched.resolved_at = now
            unmatched.save(update_fields=[
                "resolved", "resolved_by_application", "resolved_at",
            ])

        logger.info(
            "Unmatched %s assigned to application %s by user %s",
            unmatched.pk, application.pk, request.user.pk,
        )

        return redirect("cvs:inbox")


class ConfirmCVReviewView(LoginRequiredMixin, View):
    """
    POST handler: confirm a medium-confidence CV match (remove needs_review flag).

    POST params:
        cv_upload_id : int  (CVUpload PK)
    """

    def post(self, request):
        cv_upload_id = request.POST.get("cv_upload_id")
        if not cv_upload_id:
            return HttpResponseBadRequest("Missing cv_upload_id.")

        cv = get_object_or_404(CVUpload, pk=cv_upload_id, needs_review=True)
        cv.needs_review = False
        cv.save(update_fields=["needs_review"])

        logger.info(
            "CV %s confirmed by user %s", cv.pk, request.user.pk,
        )

        return redirect("cvs:inbox")


class ReassignCVView(LoginRequiredMixin, View):
    """
    POST handler: reassign a CVUpload from its current application to a
    different one. The original application reverts; the new one advances.

    POST params:
        cv_upload_id   : int  (CVUpload PK)
        application_id : int  (new Application PK)
    """

    def post(self, request):
        cv_upload_id = request.POST.get("cv_upload_id")
        application_id = request.POST.get("application_id")

        if not cv_upload_id or not application_id:
            return HttpResponseBadRequest("Missing cv_upload_id or application_id.")

        cv = get_object_or_404(CVUpload, pk=cv_upload_id)
        new_application = get_object_or_404(Application, pk=application_id)

        now = timezone.now()

        with transaction.atomic():
            cv.application = new_application
            cv.match_method = CVUpload.MatchMethod.MANUAL
            cv.needs_review = False
            cv.save(update_fields=[
                "application", "match_method", "needs_review",
            ])

            advance_application_status(new_application)

        logger.info(
            "CV %s reassigned to application %s by user %s",
            cv.pk, new_application.pk, request.user.pk,
        )

        return redirect("cvs:inbox")


