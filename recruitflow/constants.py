"""
recruitflow/constants.py

Central repository for cross-cutting, operationally-tunable constants.

Rules for what belongs here:
  - Pure Python only — no Django model imports (prevents circular import risk).
  - Referenced by more than one module, or genuinely tunable at the ops level.

What intentionally stays elsewhere:
  - TextChoices on models     — Django convention, DB-validated.
  - AWAITING_CV_STATUSES      — cvs/constants.py (imports Application.Status).
  - ElevenLabs API URLs       — calls/services.py (single consumer).
  - Fallback poll endpoints   — scheduler/jobs.py (single consumer).
"""

# ── Cache ──────────────────────────────────────────────────────────────────────

# Key used by the sidebar context processor and invalidated on every status
# change (applications/models.py) and on bulk mutations across views.
SIDEBAR_CACHE_KEY = "sidebar_counts"

# Seconds the sidebar counts are cached between requests.
SIDEBAR_CACHE_TTL = 60

# ── ElevenLabs batch calling ───────────────────────────────────────────────────

# Maximum recipients submitted in a single batch-calling API request.
# Spec § 9: "Queues are split into chunks of 50 recipients maximum."
BATCH_CHUNK_SIZE = 50

# ── Scheduler thresholds ───────────────────────────────────────────────────────

# Minutes a call may remain in initiated/in_progress before sync_stuck_calls
# polls ElevenLabs directly as a webhook fallback.
STUCK_CALL_THRESHOLD_MINUTES = 10

# Minutes after which a batch call with no bound conversation_id is escalated
# to CALL_FAILED so the application can re-enter the retry flow.
BATCH_ORPHAN_THRESHOLD_MINUTES = 60

# ── CV matching ────────────────────────────────────────────────────────────────

# Minimum difflib SequenceMatcher ratio to accept a fuzzy candidate name match.
# Spec § 11, Priority 4 (fuzzy name) and Priority 5 (CV content extraction).
FUZZY_NAME_THRESHOLD = 0.80

# Maximum PDF pages extracted for CV content analysis (pdfplumber).
# Spec § 11, Priority 5.
PDF_MAX_PAGES = 5
