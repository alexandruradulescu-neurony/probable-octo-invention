"""
messaging/views.py

Views for:
  - Candidate reply inbox  (CandidateReply)
  - Message template CRUD  (MessageTemplate)

Routes:
  GET  /messages/                        — ReplyInboxView
  POST /messages/<pk>/read/              — MarkReplyReadView
  GET  /messages/templates/              — MessageTemplateListView
  GET  /messages/templates/<pk>/edit/    — MessageTemplateEditView
"""

import logging

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse, reverse_lazy
from django.views.generic import ListView, UpdateView, View

from messaging.forms import MessageTemplateForm
from messaging.models import CandidateReply, MessageTemplate

logger = logging.getLogger(__name__)


class ReplyInboxView(LoginRequiredMixin, ListView):
    """
    GET /messages/

    Displays inbound candidate replies grouped by sender as conversations.
    Each conversation shows: sender identity, channels used, message count,
    unread count, last message preview, and last received timestamp.
    The full message history of each conversation is expandable inline.
    """

    model               = CandidateReply
    template_name       = "messaging/inbox.html"
    context_object_name = "replies"   # kept for compat; not used by template
    paginate_by         = None        # pagination is over conversations, handled manually

    def get_queryset(self):
        return (
            CandidateReply.objects
            .select_related("candidate", "application__position")
            .order_by("-received_at")
        )

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)

        # Build per-sender conversation objects in Python.
        # Ordered by latest message first (queryset is already -received_at).
        conv_map: dict[str, dict] = {}
        for reply in self.get_queryset():
            key = reply.sender
            if key not in conv_map:
                conv_map[key] = {
                    "sender":       reply.sender,
                    "candidate":    reply.candidate,
                    "application":  reply.application,
                    "channels":     set(),
                    "last_message": reply,
                    "last_received": reply.received_at,
                    "total":        0,
                    "unread":       0,
                    "messages":     [],
                }
            conv = conv_map[key]
            conv["channels"].add(reply.channel)
            conv["total"]  += 1
            if not reply.is_read:
                conv["unread"] += 1
            conv["messages"].append(reply)
            # Candidate/application from the most recent message wins
            if reply.received_at == conv["last_received"]:
                if reply.candidate and not conv["candidate"]:
                    conv["candidate"] = reply.candidate
                if reply.application and not conv["application"]:
                    conv["application"] = reply.application

        conversations = list(conv_map.values())
        ctx["conversations"]  = conversations
        ctx["unread_count"]   = CandidateReply.objects.filter(is_read=False).count()
        return ctx


class MarkReplyReadView(LoginRequiredMixin, View):
    """
    POST /messages/<pk>/read/

    Marks a single CandidateReply as read.
    """

    def post(self, request, pk):
        reply = get_object_or_404(CandidateReply, pk=pk)
        if not reply.is_read:
            reply.is_read = True
            reply.save(update_fields=["is_read"])
            logger.info("CandidateReply %s marked as read by user %s", pk, request.user.pk)

        next_url = request.POST.get("next") or request.GET.get("next")
        if next_url:
            return redirect(next_url)
        if reply.application_id:
            return redirect("applications:detail", pk=reply.application_id)
        return redirect(reverse("messaging:inbox"))


class MarkConversationReadView(LoginRequiredMixin, View):
    """
    POST /messages/conversation/mark-read/

    Marks all CandidateReply records from a given sender as read.
    sender is passed as a POST body parameter.
    """

    def post(self, request):
        sender = (request.POST.get("sender") or "").strip()
        if sender:
            updated = CandidateReply.objects.filter(sender=sender, is_read=False).update(is_read=True)
            logger.info(
                "Conversation from %s marked as read (%s messages) by user %s",
                sender, updated, request.user.pk,
            )
        return redirect(reverse("messaging:inbox"))


class DeleteConversationView(LoginRequiredMixin, View):
    """
    POST /messages/conversation/delete/

    Deletes all CandidateReply records from a given sender.
    sender is passed as a POST body parameter.
    """

    def post(self, request):
        sender = (request.POST.get("sender") or "").strip()
        if sender:
            deleted, _ = CandidateReply.objects.filter(sender=sender).delete()
            logger.info(
                "Conversation from %s deleted (%s messages) by user %s",
                sender, deleted, request.user.pk,
            )
        return redirect(reverse("messaging:inbox"))


# ── Message Template Views ────────────────────────────────────────────────────

class MessageTemplateListView(LoginRequiredMixin, ListView):
    """
    GET /messages/templates/

    Displays all MessageTemplate rows grouped by message_type for easy scanning.
    The ordering defined on the model (message_type, channel) ensures consistent
    display without extra view-level logic.
    """

    model               = MessageTemplate
    template_name       = "messaging/message_template_list.html"
    context_object_name = "templates"

    def get_queryset(self):
        return MessageTemplate.objects.order_by("message_type", "channel")

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)

        # Group templates by message_type label for the template renderer.
        grouped: dict[str, list] = {}
        for tpl in ctx["templates"]:
            label = tpl.get_message_type_display()
            grouped.setdefault(label, []).append(tpl)
        ctx["grouped"] = grouped
        return ctx


class MessageTemplateEditView(LoginRequiredMixin, UpdateView):
    """
    GET/POST /messages/templates/<pk>/edit/

    Allows the recruiter to customise the subject and body of a single
    MessageTemplate record. message_type and channel are read-only — they
    are set once at seed time and cannot be changed via the UI.
    """

    model         = MessageTemplate
    form_class    = MessageTemplateForm
    template_name = "messaging/message_template_form.html"
    success_url   = reverse_lazy("messaging:template_list")

    def form_valid(self, form):
        messages.success(self.request, "Message template saved.")
        return super().form_valid(form)

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["placeholder_docs"] = MessageTemplate.PLACEHOLDER_DOCS
        return ctx
