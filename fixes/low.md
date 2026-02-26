# Low Priority Bug Fixes — Agent 4 Instructions

You are fixing **three low-priority bugs** in the Django project at `c:\Django\recruitflow`.
Read each affected file in full before editing. Do NOT create new files unless instructed.
After all fixes are complete, commit with message: `fix(low): L2 aggregate queries, L3 version bump, L4 log level`.

---

## L2 — _attention_required makes 6 separate COUNT queries

**File:** `recruitflow/views.py`
**Class:** `DashboardView`
**Method:** `_attention_required`

### Problem

`_attention_required` currently fires 6 individual `.count()` queries against the DB
on every dashboard page load. The similar `_pipeline_data` method was already fixed
in a previous round to use a single `.aggregate()`. This method should be treated
the same way.

Note: `callbacks_today` uses a date-range filter (not just status), and `unmatched_inbound`
and `needs_review_cvs` hit different models (`UnmatchedInbound`, `CVUpload`), so they cannot
be combined into the Application aggregate. The realistic consolidation is:

1. Merge the 4 Application status counts into a single `Application.objects.aggregate()`
2. Keep the 2 separate model queries (`UnmatchedInbound`, `CVUpload`) as they are

### Current code (approximately lines 278–306)

```python
    @staticmethod
    def _attention_required(now, today_start) -> dict:
        """
        Items that require recruiter action.
        """
        today_end = today_start + timedelta(days=1)

        return {
            "call_failures": Application.objects.filter(
                status=Application.Status.CALL_FAILED,
            ).count(),
            "cv_overdue": Application.objects.filter(
                status=Application.Status.CV_OVERDUE,
            ).count(),
            "needs_human": Application.objects.filter(
                status=Application.Status.NEEDS_HUMAN,
            ).count(),
            "callbacks_today": Application.objects.filter(
                status=Application.Status.CALLBACK_SCHEDULED,
                callback_scheduled_at__gte=today_start,
                callback_scheduled_at__lt=today_end,
            ).count(),
            "unmatched_inbound": UnmatchedInbound.objects.filter(
                resolved=False,
            ).count(),
            "needs_review_cvs": CVUpload.objects.filter(
                needs_review=True,
            ).count(),
        }
```

### Replacement

```python
    @staticmethod
    def _attention_required(now, today_start) -> dict:
        """
        Items that require recruiter action.

        The four Application-based counts are collapsed into a single aggregate query.
        UnmatchedInbound and CVUpload counts remain as separate queries (different models).
        """
        today_end = today_start + timedelta(days=1)

        # Single aggregate replaces 4 separate Application COUNT queries.
        app_totals = Application.objects.aggregate(
            call_failures=Count(
                "id",
                filter=Q(status=Application.Status.CALL_FAILED),
            ),
            cv_overdue=Count(
                "id",
                filter=Q(status=Application.Status.CV_OVERDUE),
            ),
            needs_human=Count(
                "id",
                filter=Q(status=Application.Status.NEEDS_HUMAN),
            ),
            callbacks_today=Count(
                "id",
                filter=Q(
                    status=Application.Status.CALLBACK_SCHEDULED,
                    callback_scheduled_at__gte=today_start,
                    callback_scheduled_at__lt=today_end,
                ),
            ),
        )

        return {
            "call_failures":    app_totals["call_failures"],
            "cv_overdue":       app_totals["cv_overdue"],
            "needs_human":      app_totals["needs_human"],
            "callbacks_today":  app_totals["callbacks_today"],
            "unmatched_inbound": UnmatchedInbound.objects.filter(resolved=False).count(),
            "needs_review_cvs":  CVUpload.objects.filter(needs_review=True).count(),
        }
```

### Required imports

Check that `Count` and `Q` are already imported in `recruitflow/views.py`.
Look at the top-level imports. They are already used by `_pipeline_data` and `_position_summaries`,
so they should already be there. If not, add:

```python
from django.db.models import Count, Q
```

### Verification

After the edit:
- `_attention_required` contains exactly ONE `Application.objects.aggregate(...)` call
- `Count` and `Q` are imported at module level in `recruitflow/views.py`
- The returned dict still contains all 6 keys: `call_failures`, `cv_overdue`, `needs_human`,
  `callbacks_today`, `unmatched_inbound`, `needs_review_cvs`
- The returned values are identical in meaning to the original

---

## L3 — PromptTemplateUpdateView increments version even when content is unchanged

**File:** `prompts/views.py`
**Class:** `PromptTemplateUpdateView`
**Method:** `form_valid`

### Problem

The current code unconditionally increments the version number:

```python
    def form_valid(self, form):
        form.instance.version = form.instance.version + 1
        messages.success(self.request, "Prompt template saved (new version).")
        return super().form_valid(form)
```

If a recruiter opens the edit form and clicks Save without changing anything, the version
still increments. This creates meaningless audit trail entries and inflates version numbers.

### Fix

Only increment the version if the form actually has changes. Django's `ModelForm.has_changed()`
method returns `True` if any field value differs from the initial (database) value.

### Exact change in `prompts/views.py`

Replace:

```python
    def form_valid(self, form):
        form.instance.version = form.instance.version + 1
        messages.success(self.request, "Prompt template saved (new version).")
        return super().form_valid(form)
```

With:

```python
    def form_valid(self, form):
        if form.has_changed():
            form.instance.version = form.instance.version + 1
            messages.success(self.request, "Prompt template saved (new version).")
        else:
            messages.info(self.request, "No changes detected — template unchanged.")
        return super().form_valid(form)
```

### Verification

After the edit:
- `form_valid` checks `form.has_changed()` before incrementing `form.instance.version`
- A "no changes" branch shows an info message rather than a success message
- If changes ARE present, the success message and version increment work as before

---

## L4 — evaluations logger hardcoded to DEBUG instead of LOG_LEVEL

**File:** `recruitflow/settings.py`

### Problem

The `evaluations` logger is the only app-level logger hardcoded to `"DEBUG"`:

```python
"evaluations": {"handlers": ["console"], "level": "DEBUG", "propagate": False},
```

All other app loggers (applications, calls, candidates, etc.) use the configurable `LOG_LEVEL`
variable (default `"INFO"`). In production, this causes Claude request/response dumps —
including full candidate transcripts, form answers, and system prompts — to be written to
the console log permanently, even when `LOG_LEVEL=INFO` is set.

### Fix

Change `"level": "DEBUG"` to `"level": LOG_LEVEL` for the `evaluations` logger.

### Exact change in `recruitflow/settings.py`

Find this line inside the `LOGGING["loggers"]` dict:

```python
"evaluations": {"handlers": ["console"], "level": "DEBUG", "propagate": False},
```

Replace with:

```python
"evaluations": {"handlers": ["console"], "level": LOG_LEVEL, "propagate": False},
```

### Verification

After the edit:
- The `evaluations` logger uses `LOG_LEVEL` (not the string `"DEBUG"`)
- All other loggers in the `LOGGING["loggers"]` dict are unchanged
- `LOG_LEVEL` is already defined earlier in `settings.py` as `env("LOG_LEVEL", default="INFO")`
