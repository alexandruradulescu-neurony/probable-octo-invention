# High Priority Bug Fixes — Agent 2 Instructions

You are fixing **four high-priority bugs** in the Django project at `c:\Django\recruitflow`.
Read each affected file in full before editing. Do NOT create new files unless instructed.
After all fixes are complete, commit with message: `fix(high): H1 paused-position calls, H2 cache, H3 DRY reply, H4 CV size limit`.

---

## H1 — process_call_queue places calls for paused/closed positions

**File:** `scheduler/jobs.py`
**Function:** `process_call_queue`

### Problem

The `process_call_queue` job collects `CALL_QUEUED` applications without filtering on
`position__status`. This means applications linked to **paused** or **closed** positions
still receive outbound calls. Per spec §12.3, pausing a position should stop calls.

The same issue affects the `CALLBACK_SCHEDULED` query a few lines below.

### Fix

Add `.filter(position__status=Position.Status.OPEN)` to BOTH queries in `process_call_queue`.

### What to import

`Position` is already imported in `scheduler/jobs.py` if used elsewhere. Check the imports.
If not present, add: `from positions.models import Position`

### Exact changes in `scheduler/jobs.py`

**Change 1** — The `CALL_QUEUED` batch query (approximately lines 93–97):

```python
    queued = list(
        Application.objects
        .filter(status=Application.Status.CALL_QUEUED)
        .select_related("candidate", "position")
    )
```

Replace with:

```python
    queued = list(
        Application.objects
        .filter(
            status=Application.Status.CALL_QUEUED,
            position__status=Position.Status.OPEN,
        )
        .select_related("candidate", "position")
    )
```

**Change 2** — The `CALLBACK_SCHEDULED` query (find the query that filters on
`status=Application.Status.CALLBACK_SCHEDULED` and `callback_scheduled_at__lte=now`):

Add `, position__status=Position.Status.OPEN,` to its `.filter(...)` call.

### Verification

After the edit:
- Both queries (CALL_QUEUED batch and CALLBACK_SCHEDULED) have `position__status=Position.Status.OPEN`
- `from positions.models import Position` is in the file's imports

---

## H2 — BulkDeletePositionsView does not invalidate sidebar cache

**File:** `positions/views.py`
**Class:** `BulkDeletePositionsView`
**Method:** `post`

### Problem

When positions are bulk-deleted, the cascade also deletes associated applications.
However, `cache.delete(SIDEBAR_CACHE_KEY)` is never called, so the sidebar badges
(position count, application count, qualified application count) remain stale for up to 60s.

### Fix

Import `cache` and `SIDEBAR_CACHE_KEY`, then call `cache.delete(SIDEBAR_CACHE_KEY)` after
the delete.

### Exact changes in `positions/views.py`

**Step 1** — Add imports at the module level (with existing imports):

```python
from django.core.cache import cache
from recruitflow.context_processors import SIDEBAR_CACHE_KEY
```

**Step 2** — In `BulkDeletePositionsView.post`, after the `Position.objects.filter(...).delete()` line:

```python
        count, _ = Position.objects.filter(pk__in=pks).delete()
        cache.delete(SIDEBAR_CACHE_KEY)   # <-- add this line
        messages.success(request, f"Deleted {count} record(s) (positions and related applications).")
```

### Verification

After the edit:
- `from django.core.cache import cache` is at module level
- `from recruitflow.context_processors import SIDEBAR_CACHE_KEY` is at module level
- `cache.delete(SIDEBAR_CACHE_KEY)` is called immediately after the bulk delete

---

## H3 — Duplicate _save_candidate_reply / _save_email_reply logic

**Files:** `webhooks/views.py`, `scheduler/jobs.py`, `messaging/services.py`

### Problem

`webhooks/views.py` contains `_save_candidate_reply(sender, channel, body, subject, external_id)`
and `scheduler/jobs.py` contains `_save_email_reply(sender, body, subject, external_id)`.

Both functions:
1. Look up the candidate by phone or email
2. Find the most recent open application
3. Create a `CandidateReply`
4. Log the result

The logic is duplicated. A bug fix must be applied in two places.

### Fix

1. Create a shared function `save_candidate_reply(...)` in `messaging/services.py`
2. Remove the private `_save_candidate_reply` from `webhooks/views.py` and replace its call sites
3. Remove the private `_save_email_reply` from `scheduler/jobs.py` and replace its call sites

### Step 1 — Add to `messaging/services.py`

At the bottom of the file (after all existing functions), add:

```python
# ─────────────────────────────────────────────────────────────────────────────
# Inbound reply persistence (shared between webhook and scheduler)
# ─────────────────────────────────────────────────────────────────────────────

def save_candidate_reply(
    *,
    sender: str,
    channel: str,
    body: str,
    subject: str = "",
    external_id: str | None = None,
) -> None:
    """
    Persist an inbound message (email or WhatsApp) as a CandidateReply.

    Resolves the sender to a Candidate and their most recent open Application.
    Both FKs are optional — an unmatched sender still produces a record.

    Args:
        sender:      Raw phone number (WhatsApp) or email address (email).
        channel:     "email" or "whatsapp" — must match CandidateReply.Channel choices.
        body:        Plain-text message body.
        subject:     Email subject line (ignored for WhatsApp).
        external_id: Message-platform-specific ID for deduplication.
    """
    from applications.models import Application as App
    from candidates.services import lookup_candidate_by_email, lookup_candidate_by_phone
    from messaging.models import CandidateReply

    _logger = logging.getLogger(__name__)

    try:
        if "@" in sender:
            candidate = lookup_candidate_by_email(sender)
        else:
            candidate = lookup_candidate_by_phone(sender)

        application = None
        if candidate:
            application = (
                App.objects
                .filter(candidate=candidate)
                .exclude(status=App.Status.CLOSED)
                .order_by("-updated_at")
                .first()
            )

        CandidateReply.objects.create(
            candidate=candidate,
            application=application,
            channel=channel,
            sender=sender,
            subject=subject,
            body=body,
            external_id=external_id,
        )
        _logger.info(
            "CandidateReply saved: channel=%s sender=%s candidate=%s application=%s",
            channel,
            sender,
            candidate.pk if candidate else None,
            application.pk if application else None,
        )
    except Exception as exc:
        _logger.error(
            "Failed to save CandidateReply for sender=%s: %s", sender, exc, exc_info=True
        )
```

### Step 2 — Update `webhooks/views.py`

**Remove** the entire `_save_candidate_reply` function definition from `webhooks/views.py`.

**Add** this import at the top of `webhooks/views.py` (with existing imports):

```python
from messaging.services import save_candidate_reply
```

**Replace** every call to `_save_candidate_reply(...)` with `save_candidate_reply(...)`.
The call sites in `webhooks/views.py` use keyword arguments that must be preserved.
Check the exact call signatures in the file and ensure they match the new function's
keyword-only signature.

Original calls look like:
```python
_save_candidate_reply(
    sender=sender,
    channel="whatsapp",
    body=text,
    external_id=msg.get("id"),
)
```
Replace with:
```python
save_candidate_reply(
    sender=sender,
    channel="whatsapp",
    body=text,
    external_id=msg.get("id"),
)
```

Find ALL call sites and update them all.

### Step 3 — Update `scheduler/jobs.py`

**Remove** the entire `_save_email_reply` function definition from `scheduler/jobs.py`.

**Add** this import at the top of `scheduler/jobs.py` (with existing imports):

```python
from messaging.services import save_candidate_reply
```

**Replace** every call to `_save_email_reply(sender, body_snippet, subject, external_id=msg["id"])`
with:

```python
save_candidate_reply(
    sender=sender,
    channel="email",
    body=body_snippet,
    subject=subject,
    external_id=msg["id"],
)
```

There are two call sites in `scheduler/jobs.py` (around lines 737 and 753). Update both.

### Verification

After the edit:
- `save_candidate_reply` is defined once in `messaging/services.py`
- `_save_candidate_reply` function NO LONGER exists in `webhooks/views.py`
- `_save_email_reply` function NO LONGER exists in `scheduler/jobs.py`
- All 2+ call sites in `webhooks/views.py` use `save_candidate_reply`
- All 2 call sites in `scheduler/jobs.py` use `save_candidate_reply`
- The `from messaging.services import save_candidate_reply` import is in both files

---

## H4 — No file-size limit on manual CV upload

**File:** `applications/forms.py`
**Class:** `ManualCVUploadForm`

### Problem

The form validates file extension (`.pdf`) but not file size. A malicious or accidental
upload of a very large PDF would be saved to `MEDIA_ROOT/cvs/` and consume disk space,
potentially causing an outage.

### Fix

Add a `clean_cv_file` method to `ManualCVUploadForm` that enforces a 10 MB limit.

### Exact change in `applications/forms.py`

After the existing `cv_file` field definition in `ManualCVUploadForm`, add:

```python
class ManualCVUploadForm(forms.Form):
    """Manually upload a CV file for this application."""
    MAX_CV_SIZE_MB = 10

    cv_file = forms.FileField(
        validators=[FileExtensionValidator(allowed_extensions=["pdf"])],
        widget=forms.ClearableFileInput(attrs={
            "class": "form-control form-control-sm",
            "accept": ".pdf",
        }),
    )

    def clean_cv_file(self):
        f = self.cleaned_data.get("cv_file")
        if f and f.size > self.MAX_CV_SIZE_MB * 1024 * 1024:
            raise forms.ValidationError(
                f"CV file is too large ({f.size // (1024 * 1024)} MB). "
                f"Maximum allowed size is {self.MAX_CV_SIZE_MB} MB."
            )
        return f
```

Do NOT change any other form in `applications/forms.py`. Only `ManualCVUploadForm` needs
the `clean_cv_file` method added.

### Verification

After the edit:
- `ManualCVUploadForm` has a `MAX_CV_SIZE_MB = 10` class attribute
- `ManualCVUploadForm` has a `clean_cv_file` method that raises `ValidationError` for oversized files
- All other forms in the file are unchanged
