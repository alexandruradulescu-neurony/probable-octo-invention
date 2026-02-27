"""
applications/templatetags/rf_tags.py

Custom template filters for the RecruitFlow application templates.
"""
from django import template

register = template.Library()

# ── Status → timeline dot colour ─────────────────────────────────────────────

_STATUS_COLORS = {
    # Pre-call
    "pending_call": "#94A3B8",
    "call_queued": "#94A3B8",
    # In-call
    "call_in_progress": "#6366F1",
    "call_completed": "#6366F1",
    "call_failed": "#EF4444",
    # Scoring
    "scoring": "#3B82F6",
    # Qualified path
    "qualified": "#10B981",
    "awaiting_cv": "#F59E0B",
    "cv_followup_1": "#F59E0B",
    "cv_followup_2": "#F59E0B",
    "cv_overdue": "#EF4444",
    "cv_received": "#10B981",
    # Not-qualified path
    "not_qualified": "#EF4444",
    "awaiting_cv_rejected": "#F97316",
    "cv_received_rejected": "#EF4444",
    # Special
    "callback_scheduled": "#A855F7",
    "needs_human": "#F97316",
    # Terminal
    "closed": "#94A3B8",
}


@register.filter
def status_dot_color(status: str) -> str:
    """Return a hex color for a given application status value."""
    return _STATUS_COLORS.get(status, "#94A3B8")


# ── SVG gauge arc length ──────────────────────────────────────────────────────

@register.filter
def score_arc(score) -> int:
    """
    Convert a 0–100 score into the SVG stroke-dasharray filled length.
    The gauge circle has r=34, circumference ≈ 214.
    """
    try:
        return round(int(score) * 214 / 100)
    except (TypeError, ValueError):
        return 0


@register.filter
def score_stroke_color(outcome: str) -> str:
    """Return a CSS color for the gauge ring based on outcome."""
    mapping = {
        "qualified": "#10B981",
        "not_qualified": "#EF4444",
        "callback_requested": "#F59E0B",
        "needs_human": "#F97316",
    }
    return mapping.get(outcome, "#6366F1")
