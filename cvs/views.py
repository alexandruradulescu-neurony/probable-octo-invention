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
from django.core.files.storage import default_storage
from django.db import transaction
from django.db.models import Q
from django.http import HttpResponseBadRequest, JsonResponse
from django.shortcuts import get_object_or_404, redirect
from django.utils import timezone
from django.views import View
from django.views.generic import TemplateView

from applications.models import Application
from cvs.helpers import advance_application_status, channel_to_source
from cvs.models import CVUpload, UnmatchedInbound

logger = logging.getLogger(__name__)

class CVDeleteView(LoginRequiredMixin, View):
    """
    POST /cvs/<pk>/delete/

    Deletes a CVUpload record (and all sibling records sharing the same
    file_path for that candidate) plus the physical file from storage.

    A `next` POST param controls where to redirect after deletion.
    """

    def post(self, request, pk):
        cv = get_object_or_404(CVUpload, pk=pk)
        file_path = cv.file_path
        candidate = cv.candidate

        if file_path:
            # Remove every CVUpload for this candidate that points to the same file
            # (one upload can be fanned out to multiple application records).
            CVUpload.objects.filter(candidate=candidate, file_path=file_path).delete()
            # Only delete the physical file if no other candidate references it.
            if not CVUpload.objects.filter(file_path=file_path).exists():
                try:
                    if default_storage.exists(file_path):
                        default_storage.delete(file_path)
                except Exception as exc:
                    logger.warning("Could not delete CV file %s: %s", file_path, exc)
        else:
            cv.delete()

        logger.info(
            "CV %s deleted by user %s (candidate=%s file=%s)",
            pk, request.user.pk, candidate.pk if candidate else "—", file_path,
        )

        next_url = request.POST.get("next") or "/"
        return redirect(next_url)


class ApplicationSearchView(LoginRequiredMixin, View):
    """
    GET /cvs/application-search/?q=<query>

    Returns up to 10 active applications whose candidate name, position title,
    or application ID contains the query string.  Used by the assign/reassign
    autocomplete inputs in the CV inbox.
    """

    def get(self, request):
        q = (request.GET.get("q") or "").strip()
        if not q:
            return JsonResponse({"results": []})

        filters = (
            Q(candidate__first_name__icontains=q)
            | Q(candidate__last_name__icontains=q)
            | Q(position__title__icontains=q)
        )
        # Allow direct lookup by application PK
        if q.isdigit():
            filters |= Q(pk=int(q))

        applications = (
            Application.objects
            .filter(filters)
            .exclude(status=Application.Status.CLOSED)
            .select_related("candidate", "position")
            .order_by("-created_at")[:10]
        )

        results = [
            {
                "id": app.pk,
                "label": (
                    f"{app.candidate.full_name} — {app.position.title} "
                    f"(#{app.pk} · {app.get_status_display()})"
                ),
            }
            for app in applications
        ]
        return JsonResponse({"results": results})


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
    POST handler: manually assign an UnmatchedInbound item to a candidate.

    The recruiter picks any application belonging to the target candidate.
    The system then finds ALL of that candidate's awaiting-CV applications
    and advances every one of them — identical to the auto-matching pipeline.

    A CVUpload is created for each advanced application, using the file that
    was saved to disk when the inbound was originally received.

    POST params:
        unmatched_id    : int  (UnmatchedInbound PK)
        application_id  : int  (any Application PK for the target candidate)
    """

    def post(self, request):
        unmatched_id = request.POST.get("unmatched_id")
        application_id = request.POST.get("application_id")

        if not unmatched_id or not application_id:
            return HttpResponseBadRequest("Missing unmatched_id or application_id.")

        unmatched = get_object_or_404(UnmatchedInbound, pk=unmatched_id, resolved=False)
        anchor_application = get_object_or_404(Application, pk=application_id)
        candidate = anchor_application.candidate

        source = channel_to_source(unmatched.channel)
        file_name = unmatched.attachment_name or "unknown"
        file_path = unmatched.file_path or ""

        # Find all awaiting-CV applications for this candidate (same as auto-matching)
        from cvs.constants import AWAITING_CV_STATUSES
        awaiting_apps = list(
            Application.objects
            .filter(candidate=candidate, status__in=list(AWAITING_CV_STATUSES))
            .select_related("candidate", "position")
        )

        # If none are awaiting a CV, fall back to just the anchor application so
        # the CVUpload is still created and the unmatched item is resolved.
        target_apps = awaiting_apps or [anchor_application]

        now = timezone.now()
        advanced_count = 0

        with transaction.atomic():
            for app in target_apps:
                CVUpload.objects.create(
                    candidate=candidate,
                    application=app,
                    file_name=file_name,
                    file_path=file_path,
                    source=source,
                    match_method=CVUpload.MatchMethod.MANUAL,
                    needs_review=False,
                )
                advanced = advance_application_status(app)
                if advanced:
                    advanced_count += 1

            unmatched.resolved = True
            unmatched.resolved_by_application = anchor_application
            unmatched.resolved_at = now
            unmatched.save(update_fields=[
                "resolved", "resolved_by_application", "resolved_at",
            ])

        logger.info(
            "Unmatched %s assigned to candidate %s (%s app(s) advanced) by user %s",
            unmatched.pk, candidate.pk, advanced_count, request.user.pk,
        )

        if not awaiting_apps:
            from django.contrib import messages as msg_framework
            msg_framework.warning(
                request,
                f"CV assigned to {candidate.full_name} but no applications were in an "
                "awaiting-CV status — status was not changed.",
            )
        elif advanced_count:
            from django.contrib import messages as msg_framework
            msg_framework.success(
                request,
                f"CV assigned to {candidate.full_name}: "
                f"{advanced_count} application(s) advanced to CV Received.",
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


