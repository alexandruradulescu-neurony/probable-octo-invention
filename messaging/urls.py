"""
messaging/urls.py

  GET  /messages/                           — candidate reply inbox (grouped)
  POST /messages/<pk>/read/                 — mark a single reply as read
  POST /messages/conversation/mark-read/    — mark all replies from a sender as read
  POST /messages/conversation/delete/       — delete all replies from a sender
  GET  /messages/templates/                 — message template list
  GET/POST /messages/templates/<pk>/edit/   — edit a message template
"""

from django.urls import path

from messaging import views

app_name = "messaging"

urlpatterns = [
    path("", views.ReplyInboxView.as_view(), name="inbox"),
    path("<int:pk>/read/", views.MarkReplyReadView.as_view(), name="reply_read"),

    # Conversation-level actions (sender passed in POST body)
    path("conversation/mark-read/", views.MarkConversationReadView.as_view(), name="conversation_mark_read"),
    path("conversation/delete/", views.DeleteConversationView.as_view(), name="conversation_delete"),

    # Message Templates
    path("templates/", views.MessageTemplateListView.as_view(), name="template_list"),
    path("templates/<int:pk>/edit/", views.MessageTemplateEditView.as_view(), name="template_edit"),
]
