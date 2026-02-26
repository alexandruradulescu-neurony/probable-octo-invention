# Critical Bug Fixes — Agent 1 Instructions

You are fixing **two critical bugs** in the Django project at `c:\Django\recruitflow`.
Read each affected file in full before editing. Do NOT create new files unless instructed.
After all fixes are complete, commit with message: `fix(critical): C1 atomic savepoint, C2 evaluate_call race condition`.

---

## C1 — Bulk move: IntegrityError inside atomic() without savepoint poisons the transaction

**File:** `applications/views.py`
**Class:** `BulkActionApplicationsView`
**Method:** `post`

### Problem

In PostgreSQL, when an `IntegrityError` is raised inside a `transaction.atomic()` block,
the entire transaction is poisoned. Any subsequent DB operation raises:
`django.db.utils.InternalError: current transaction is aborted, commands ignored until end of transaction block`

The current code wraps the entire loop in a single `with db_transaction.atomic():` and catches
`IntegrityError` inside the loop. After the first conflict, every subsequent app.save() fails
silently, so only the first non-conflicting application may succeed.

### Fix

Add a **nested** `with db_transaction.atomic():` savepoint inside the loop around each
individual application's move operation. Django creates a PostgreSQL SAVEPOINT for nested
atomic blocks, so a failed inner block is rolled back to the savepoint, and the outer
transaction continues cleanly.

### Exact change in `applications/views.py`

Find this block (approximately lines 267–282):

```python
                    with db_transaction.atomic():
                        for app in qs.select_related("position"):
                            try:
                                old_title = app.position.title
                                app.position = pos
                                app.save(update_fields=["position", "updated_at"])
                                StatusChange.objects.create(
                                    application=app,
                                    from_status=app.status,
                                    to_status=app.status,
                                    changed_by=request.user,
                                    note=f"Application moved from position '{old_title}' to '{pos.title}'",
                                )
                                moved += 1
                            except IntegrityError:
                                conflict += 1
```

Replace with:

```python
                    with db_transaction.atomic():
                        for app in qs.select_related("position"):
                            try:
                                with db_transaction.atomic():  # savepoint per app
                                    old_title = app.position.title
                                    app.position = pos
                                    app.save(update_fields=["position", "updated_at"])
                                    StatusChange.objects.create(
                                        application=app,
                                        from_status=app.status,
                                        to_status=app.status,
                                        changed_by=request.user,
                                        note=f"Application moved from position '{old_title}' to '{pos.title}'",
                                    )
                                    moved += 1
                            except IntegrityError:
                                conflict += 1
```

### Verification

After the edit, the structure should be:
- Outer `with db_transaction.atomic()` wraps the whole loop
- Inner `with db_transaction.atomic()` wraps each individual application's save + StatusChange
- `except IntegrityError` is at the same indentation level as the inner `with`

---

## C2 — evaluate_call TOCTOU race: idempotency check outside atomic() allows duplicate evaluations

**File:** `evaluations/services.py`
**Class:** `ClaudeService`
**Method:** `evaluate_call`

### Problem

The current idempotency guard:
```python
existing = LLMEvaluation.objects.filter(call=call).first()
if existing:
    return existing
```
is **outside** the `transaction.atomic()` block. If two threads (ElevenLabs webhook + sync_stuck_calls
scheduler) both reach this check at the same time, both see `existing=None`, both proceed to call
Claude, and both create an `LLMEvaluation`. This causes:
- Duplicate Claude API costs
- Two status transitions (e.g. `set_qualified` called twice)
- Two outbound CV-request messages sent to the candidate

### Fix

Move the idempotency check **inside** the `transaction.atomic()` block using `select_for_update()`
on the `Call` record to lock it. This ensures only one thread can proceed past the check at a time.

Also: all the pre-processing code (building prompts, calling Claude API) must remain **outside**
the atomic block — only the DB write and final idempotency re-check go inside.

### Exact change in `evaluations/services.py`

The current structure is (approximately lines 219–296):

```python
        existing = LLMEvaluation.objects.filter(call=call).first()
        if existing:
            logger.info(
                "Evaluation already exists for call=%s (evaluation=%s) — skipping duplicate",
                call.pk, existing.pk,
            )
            return existing

        application = call.application
        position = application.position
        candidate = application.candidate

        # ... build prompts, validate ...

        raw = self._send_message(...)  # Claude API call — OUTSIDE atomic, this is correct

        data = _parse_claude_json(raw)

        # ... validate data ...

        callback_at = _parse_optional_datetime(data.get("callback_at"))

        with transaction.atomic():
            evaluation = LLMEvaluation.objects.create(...)
            # ... application status transitions ...
            application.save(...)
```

Replace with this pattern:

```python
        # First fast-path check (avoids Claude API cost on obvious duplicates).
        # Not race-safe by itself — a definitive re-check is done inside atomic() below.
        if LLMEvaluation.objects.filter(call=call).exists():
            existing = LLMEvaluation.objects.filter(call=call).first()
            logger.info(
                "Evaluation already exists for call=%s (evaluation=%s) — skipping duplicate",
                call.pk, existing.pk,
            )
            return existing

        application = call.application
        position = application.position
        candidate = application.candidate

        # ... (keep all existing prompt building and validation code unchanged) ...

        raw = self._send_message(...)   # Claude API call — must remain outside atomic()

        data = _parse_claude_json(raw)

        # ... (keep all existing data validation unchanged) ...

        callback_at = _parse_optional_datetime(data.get("callback_at"))

        with transaction.atomic():
            # Lock the Call row to serialise concurrent webhook + scheduler deliveries.
            # Re-check for an existing evaluation inside the lock to close the TOCTOU window.
            Call.objects.select_for_update().get(pk=call.pk)
            existing = LLMEvaluation.objects.filter(call=call).first()
            if existing:
                logger.info(
                    "Evaluation already exists for call=%s (evaluation=%s) — skipping duplicate (race prevented)",
                    call.pk, existing.pk,
                )
                return existing

            evaluation = LLMEvaluation.objects.create(...)
            # ... (keep all existing application status transitions unchanged) ...
            application.save(...)
```

### Implementation notes

- You must import `Call` inside `evaluations/services.py` if not already imported.
  Check the current imports at the top of the file. `from calls.models import Call` may
  need to be added.
- The `Call.objects.select_for_update().get(pk=call.pk)` result does NOT need to be used —
  it exists solely as a row-level advisory lock. The `call` variable already has the data needed.
- Do NOT move the Claude API call (`self._send_message(...)`) inside the atomic block.
  Claude can take several seconds; holding a DB row lock for that long would cause deadlocks.
- Keep all logging and existing code unchanged except for the structural changes above.

### Verification

After the edit:
1. `LLMEvaluation.objects.filter(call=call).exists()` check exists BEFORE `transaction.atomic()`
2. `Call.objects.select_for_update().get(pk=call.pk)` exists as the FIRST statement INSIDE `transaction.atomic()`
3. A second `LLMEvaluation.objects.filter(call=call).first()` check exists INSIDE `transaction.atomic()`, immediately after the select_for_update
4. `LLMEvaluation.objects.create(...)` comes after the inner check
5. The `self._send_message(...)` call remains OUTSIDE `transaction.atomic()`
6. Check that `from calls.models import Call` is in the imports at the top of the file
