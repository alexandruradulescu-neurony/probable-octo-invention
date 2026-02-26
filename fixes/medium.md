# Medium Priority Bug Fixes — Agent 3 Instructions

You are fixing **five medium-priority bugs** in the Django project at `c:\Django\recruitflow`.
Read each affected file in full before editing. Do NOT create new files unless instructed.
After all fixes are complete, commit with message: `fix(medium): M1 AWAITING_CV_STATUSES dedup, M2 whapi timeout, M3 reply exception, M4 note docstring, M5 import cleanup`.

---

## M1 — DashboardView.AWAITING_CV_STATUSES duplicates cvs/constants.py

**File:** `recruitflow/views.py`
**Class:** `DashboardView`

### Problem

`DashboardView` defines its own `AWAITING_CV_STATUSES` class attribute as a plain Python `set`.
The authoritative definition is `cvs.constants.AWAITING_CV_STATUSES` (a `frozenset`).
If a new CV-awaiting status is ever added to `cvs/constants.py`, the dashboard grouping
would silently diverge.

### Fix

Import `AWAITING_CV_STATUSES` from `cvs.constants` and assign it to the class attribute.

### Exact changes in `recruitflow/views.py`

**Step 1** — Add this import at the top of the file (with existing imports):

```python
from cvs.constants import AWAITING_CV_STATUSES as _AWAITING_CV_STATUSES_CONST
```

**Step 2** — In `DashboardView`, find the class attribute:

```python
    AWAITING_CV_STATUSES = {
        Application.Status.AWAITING_CV,
        Application.Status.CV_FOLLOWUP_1,
        Application.Status.CV_FOLLOWUP_2,
        Application.Status.CV_OVERDUE,
        Application.Status.AWAITING_CV_REJECTED,
    }
```

Replace with:

```python
    AWAITING_CV_STATUSES = _AWAITING_CV_STATUSES_CONST
```

**Step 3** — Verify that `DashboardView._pipeline_data` and `DashboardView._position_summaries`
still work correctly. They reference `cls.AWAITING_CV_STATUSES` or `awaiting_cv_statuses`
(a local variable set from the class attribute). The frozenset is compatible with all
Django ORM `__in` lookups, so no further changes are needed.

### Verification

After the edit:
- `from cvs.constants import AWAITING_CV_STATUSES as _AWAITING_CV_STATUSES_CONST` is in imports
- `DashboardView.AWAITING_CV_STATUSES` is assigned from `_AWAITING_CV_STATUSES_CONST`
- The inline set literal is removed from the class body
- `_pipeline_data` and `_position_summaries` continue to function (they use `cls.AWAITING_CV_STATUSES`)

---

## M2 — Whapi webhook: slow media download can block response beyond Whapi timeout

**File:** `webhooks/views.py`
**Function:** `_download_whapi_media`

### Problem

`_download_whapi_media` has a 30-second timeout. If a Whapi payload contains multiple media
messages, one slow download blocks all subsequent messages AND delays the HTTP 200 response
to Whapi. Whapi typically re-delivers if no response is received within its own timeout
(15–30 seconds), causing duplicate processing.

Without a full async infrastructure (Celery), the best available fix is:
1. Reduce the per-download timeout to 15 seconds
2. Add per-message error isolation so one failed download doesn't prevent other messages
   from being processed

The per-message isolation is already partially in place via the `try/except` in
`_handle_whapi_message`. We only need to reduce the timeout.

### Exact change in `webhooks/views.py`

Find `_download_whapi_media` and locate the `timeout=30` parameter:

```python
        resp = http_requests.get(url, headers=headers, timeout=30)
```

Replace with:

```python
        resp = http_requests.get(url, headers=headers, timeout=15)
```

### Verification

After the edit:
- `_download_whapi_media` uses `timeout=15`

---

## M3 — _save_candidate_reply swallows all exceptions permanently losing messages

**File:** `webhooks/views.py`
**Function:** `_save_candidate_reply`

### Problem

**IMPORTANT: Check first whether H3 from `fixes/high.md` has already been applied.**
If `_save_candidate_reply` no longer exists in `webhooks/views.py` (because H3 moved it to
`messaging/services.py` as `save_candidate_reply`), then apply this fix to
`messaging/services.py` `save_candidate_reply` instead.

The current code wraps `CandidateReply.objects.create(...)` in a bare `except Exception`
that logs and returns `None`. For WhatsApp text-message replies (non-media), if the DB
write fails, the webhook still returns HTTP 200 and Whapi will NOT retry. The message
is permanently lost.

The correct behaviour: swallow errors only for non-critical failures (e.g. candidate lookup
returning None), but let unexpected errors (e.g. DB down) propagate up to the webhook so
Whapi receives a 5xx and retries.

### Fix

In the `_save_candidate_reply` function (or `save_candidate_reply` in `messaging/services.py`
if H3 was applied):

Keep the broad `except Exception` only for the **candidate lookup and application lookup**
steps. Move `CandidateReply.objects.create(...)` **outside** the try/except so DB errors
propagate naturally.

### Exact refactor

Current structure:
```python
def _save_candidate_reply(...) -> None:
    try:
        candidate = lookup_candidate_by_...()
        application = None
        if candidate:
            application = App.objects.filter(...).first()

        CandidateReply.objects.create(...)
        logger.info(...)
    except Exception as exc:
        logger.error(...)
```

Replace with:

```python
def _save_candidate_reply(...) -> None:  # or save_candidate_reply
    # Resolve sender to candidate + application — failures are non-fatal
    candidate = None
    application = None
    try:
        if "@" in sender:
            candidate = lookup_candidate_by_email(sender)
        else:
            candidate = lookup_candidate_by_phone(sender)

        if candidate:
            application = (
                App.objects
                .filter(candidate=candidate)
                .exclude(status=App.Status.CLOSED)
                .order_by("-updated_at")
                .first()
            )
    except Exception as exc:
        logger.warning(
            "Candidate/application lookup failed for sender=%s: %s", sender, exc, exc_info=True
        )

    # DB write: let this propagate so the caller (webhook) can return a 5xx for retry
    CandidateReply.objects.create(
        candidate=candidate,
        application=application,
        channel=channel,
        sender=sender,
        subject=subject,
        body=body,
        external_id=external_id,
    )
    logger.info(
        "CandidateReply saved: channel=%s sender=%s candidate=%s application=%s",
        channel,
        sender,
        candidate.pk if candidate else None,
        application.pk if application else None,
    )
```

**Note:** The `_handle_whapi_message` function in `webhooks/views.py` already wraps the
entire `cv_process_inbound` call in a `try/except`. For text-message replies, the exception
will bubble up to `whapi_webhook`'s `for msg in messages:` loop. That loop does NOT have
a try/except, so the exception will propagate further and return a 500 to Whapi, which is
correct. Whapi will retry delivery.

### Verification

After the edit:
- Only the candidate/application lookup is inside a try/except
- `CandidateReply.objects.create(...)` is outside any try/except (at function scope)
- The function is either `_save_candidate_reply` in `webhooks/views.py` (if H3 wasn't applied)
  or `save_candidate_reply` in `messaging/services.py` (if H3 was applied)

---

## M4 — AddNoteView bypasses transition helpers (design clarity)

**File:** `applications/views.py`
**Class:** `AddNoteView`
**Method:** `post`

### Problem

`AddNoteView.post` creates a `StatusChange` record directly, bypassing `Application.change_status`.
This is intentional (a note doesn't change status, and `change_status` returns early when
`old_status == new_status`), but it's undocumented. It creates confusion: is this a bug or
intentional? Using `StatusChange` as a general timeline entry also blurs the model's purpose.

### Fix

Add a clear docstring/comment to both `AddNoteView` and `AddNoteView.post` explaining
the deliberate bypass. Also add an inline comment on the `StatusChange.objects.create` call.
No logic changes required.

### Exact changes in `applications/views.py`

Find `AddNoteView` and update the docstring and the method:

```python
class AddNoteView(LoginRequiredMixin, View):
    """
    POST /applications/<pk>/add-note/

    Adds a free-text note to the application's status timeline.

    Deliberately creates a StatusChange record with from_status == to_status (no
    actual transition) rather than calling Application.change_status(), because
    change_status() is a no-op when the status hasn't changed. The StatusChange
    model is intentionally used as the unified timeline/audit log for both status
    transitions and plain notes.
    """

    def post(self, request, pk):
        app = get_object_or_404(Application, pk=pk)
        form = AddNoteForm(request.POST)
        if form.is_valid():
            # from_status == to_status signals this is a note, not a real transition.
            StatusChange.objects.create(
                application=app,
                from_status=app.status,
                to_status=app.status,
                changed_by=request.user,
                note=form.cleaned_data["note"],
            )
            django_messages.success(request, "Note added.")
        return redirect("applications:detail", pk=pk)
```

### Verification

After the edit:
- `AddNoteView` has a class-level docstring explaining the deliberate bypass
- The `StatusChange.objects.create(...)` call has an inline comment

---

## M5 — Inline import of `messages` inside post() in BulkDeletePositionsView

**File:** `positions/views.py`
**Class:** `BulkDeletePositionsView`
**Method:** `post`

### Problem

**IMPORTANT: Check first if H2 from `fixes/high.md` has already been applied.**
If H2 was applied, `from django.contrib import messages` was likely already moved to the
module level. In that case, this fix may already be complete — verify and skip if so.

The current code has:
```python
    def post(self, request):
        from django.contrib import messages
        ...
```

Inline function-level imports are inconsistent with the rest of the project, make the import
harder to spot during refactoring, and add minor per-call overhead.

### Fix

Move `from django.contrib import messages` to the module-level imports at the top of
`positions/views.py`. Every other view file in this project imports `messages` at module level.

### Exact changes in `positions/views.py`

**Step 1** — Remove the inline import from inside `BulkDeletePositionsView.post`.

**Step 2** — Add `from django.contrib import messages` to the module-level imports.
Check if `messages` is already imported at module level (it may have been added by H2).
If already present, only remove the inline import.

### Verification

After the edit:
- `from django.contrib import messages` appears at module level (with other imports)
- There is NO `from django.contrib import messages` inside any function or method body
  in `positions/views.py`
