"""
cvs/services.py

CV-related services.

Public API:
  extract_cv_data_via_haiku(text_content)    — Claude Haiku contact extraction
  process_inbound_cv(...)                    — Smart CV matching and attachment

Spec reference: Section 11 — CV Matching Logic (Smart Matching)

Priority  Method                                      Confidence
────────  ──────────────────────────────────────────  ──────────
  1       Sender email   → exact Candidate.email      High
  2       Sender phone   → exact Candidate.phone /    High
                           whatsapp_number
  3       Subject/body   → regex Application ID       High
  4       Sender name    → fuzzy Candidate full_name  Medium
  5       CV text extract→ Claude Haiku → fuzzy match Medium
  6       No match       → UnmatchedInbound           —

Multi-application rule: one CV submission is attached to ALL open awaiting-CV
applications of the matched candidate.
"""

import io
import json
import logging
import re
import uuid
from difflib import SequenceMatcher
from pathlib import Path

import anthropic
import pdfplumber
from django.conf import settings
from django.db import transaction

from applications.models import Application
from candidates.models import Candidate
from cvs.constants import AWAITING_CV_STATUSES
from cvs.helpers import advance_application_status, channel_to_source
from cvs.models import CVUpload, UnmatchedInbound
from recruitflow.text_utils import build_full_name, strip_json_fence

logger = logging.getLogger(__name__)


# ── Constants ──────────────────────────────────────────────────────────────────

# Minimum SequenceMatcher ratio to accept a fuzzy name match.
FUZZY_NAME_THRESHOLD = 0.80

# Maximum pages to extract from a PDF for CV content analysis (spec § 11).
PDF_MAX_PAGES = 2

# Regex that captures an application reference number from free text.
# Matches patterns like:  App #42  |  Application ID: 123  |  Ref 456  |  #789
_APP_ID_RE = re.compile(
    r"(?:app(?:lication)?[\s#\-]*(?:id)?|ref(?:erence)?|#|id)\s*[:#\-]?\s*(\d+)",
    re.IGNORECASE,
)


# ─────────────────────────────────────────────────────────────────────────────
# Custom exceptions
# ─────────────────────────────────────────────────────────────────────────────

class CVExtractionError(Exception):
    """Raised when CV data extraction via Claude fails."""


# ─────────────────────────────────────────────────────────────────────────────
# Public function 1: Claude Haiku contact extraction
# ─────────────────────────────────────────────────────────────────────────────

def extract_cv_data_via_haiku(text_content: str) -> dict:
    """
    Extract candidate contact details from raw CV text using Claude Haiku.

    Uses the fast/cheap Haiku model (ANTHROPIC_FAST_MODEL) since this is a
    simple structured extraction task — no reasoning required.

    Args:
        text_content: Plain text extracted from a CV document (e.g. via pdfplumber).

    Returns:
        dict with keys: first_name, last_name, email, phone (str | None each).

    Raises:
        CVExtractionError on API failure or unparseable response.
    """
    api_key = settings.ANTHROPIC_API_KEY
    if not api_key:
        raise CVExtractionError("ANTHROPIC_API_KEY is not configured.")

    model = settings.ANTHROPIC_FAST_MODEL
    if not model:
        raise CVExtractionError("ANTHROPIC_FAST_MODEL is not configured.")

    logger.debug("Extracting CV data via Haiku (%s)", model)

    system_prompt = (
        "You are a precise data extraction assistant. "
        "Extract contact information from CV/resume text. "
        "Respond ONLY with a valid JSON object — no prose, no markdown fences."
    )

    user_message = (
        "Extract the following fields from the CV text below. "
        "If a field cannot be found, use null.\n\n"
        "Return exactly this JSON schema:\n"
        "{\n"
        '  "first_name": "<first name or null>",\n'
        '  "last_name": "<last name or null>",\n'
        '  "email": "<email address or null>",\n'
        '  "phone": "<phone number or null>"\n'
        "}\n\n"
        f"--- CV TEXT START ---\n{text_content}\n--- CV TEXT END ---"
    )

    try:
        client = anthropic.Anthropic(api_key=api_key)
        message = client.messages.create(
            model=model,
            max_tokens=256,
            system=system_prompt,
            messages=[{"role": "user", "content": user_message}],
        )
    except anthropic.APIError as exc:
        raise CVExtractionError(f"Anthropic API error during CV extraction: {exc}") from exc

    if not message.content:
        raise CVExtractionError("Anthropic returned an empty response for CV extraction.")

    raw = strip_json_fence(message.content[0].text)

    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise CVExtractionError(
            f"Failed to parse CV extraction JSON: {exc}. Raw: {raw[:200]}"
        ) from exc

    if not isinstance(data, dict):
        raise CVExtractionError(
            f"Expected JSON object from Haiku, got {type(data).__name__}."
        )

    result = {}
    for field in ("first_name", "last_name", "email", "phone"):
        value = data.get(field)
        result[field] = str(value).strip() if value else None

    logger.debug("CV extraction result: %s", result)
    return result


# ─────────────────────────────────────────────────────────────────────────────
# Public function 2: Smart CV matching
# ─────────────────────────────────────────────────────────────────────────────

def process_inbound_cv(
    channel: str,
    sender: str,
    file_name: str,
    file_content: bytes,
    text_body: str,
    subject: str = "",
    raw_payload: dict | None = None,
) -> dict:
    """
    Apply the smart matching cascade to an inbound CV submission, attach it to
    the correct Application(s), and advance their statuses.

    Args:
        channel      : "email" or "whatsapp"
        sender       : Sender email address or WhatsApp phone number (digits only)
        file_name    : Original file name, e.g. "john_smith_cv.pdf"
        file_content : Raw bytes of the CV file
        text_body    : Email body text or WhatsApp message caption
        subject      : Email subject line (optional; improves P3 matching)
        raw_payload  : Full inbound payload dict — stored in UnmatchedInbound if
                       no match is found

    Returns:
        dict:
          matched         (bool)
          confidence      "high" | "medium" | None
          method          CVUpload.MatchMethod value | None
          application_pks list[int]  — PKs of applications that received the CV
          cv_upload_pks   list[int]  — PKs of created CVUpload records
          unmatched_pk    int | None — PK of UnmatchedInbound if no match
    """
    source = channel_to_source(channel)
    sender = (sender or "").strip()

    # ── Priority 1: Exact email match ──────────────────────────────────────────
    if sender and ("@" in sender or channel == "email"):
        candidate = _match_by_email(sender)
        if candidate:
            result = _process_candidate_match(
                candidate, CVUpload.MatchMethod.EXACT_EMAIL, False,
                source, file_name, file_content,
            )
            if result:
                logger.info(
                    "CV matched P1 (exact email): candidate=%s applications=%s",
                    candidate.pk, result["application_pks"],
                )
                return result

    # ── Priority 2: Exact phone match ──────────────────────────────────────────
    if sender:
        candidate = _match_by_phone(sender)
        if candidate:
            result = _process_candidate_match(
                candidate, CVUpload.MatchMethod.EXACT_PHONE, False,
                source, file_name, file_content,
            )
            if result:
                logger.info(
                    "CV matched P2 (exact phone): candidate=%s applications=%s",
                    candidate.pk, result["application_pks"],
                )
                return result

    # ── Priority 3: Application ID in subject / body ───────────────────────────
    app_id = _extract_application_id(subject, text_body)
    if app_id is not None:
        try:
            target_app = (
                Application.objects
                .select_related("candidate")
                .get(pk=app_id)
            )
            # Apply multi-application rule: attach to all awaiting-CV apps for
            # this candidate, not just the one referenced.
            result = _process_candidate_match(
                target_app.candidate, CVUpload.MatchMethod.SUBJECT_ID, False,
                source, file_name, file_content,
            )
            if result:
                logger.info(
                    "CV matched P3 (subject ID=%s): candidate=%s applications=%s",
                    app_id, target_app.candidate_id, result["application_pks"],
                )
                return result
        except Application.DoesNotExist:
            logger.debug("P3: application ID %s from subject/body not found", app_id)

    # ── Priority 4: Fuzzy sender display-name match ────────────────────────────
    sender_name = _extract_sender_name(sender)
    if sender_name:
        candidate_pool = _get_candidates_awaiting_cv()
        candidate = _fuzzy_match_name(sender_name, candidate_pool)
        if candidate:
            result = _process_candidate_match(
                candidate, CVUpload.MatchMethod.FUZZY_NAME, True,  # needs_review
                source, file_name, file_content,
            )
            if result:
                logger.info(
                    "CV matched P4 (fuzzy name '%s'): candidate=%s applications=%s",
                    sender_name, candidate.pk, result["application_pks"],
                )
                return result

    # ── Priority 5: CV content extraction via Claude Haiku ─────────────────────
    raw_text = _extract_text_from_file(file_name, file_content)
    if raw_text.strip():
        try:
            extracted = extract_cv_data_via_haiku(raw_text)
            candidate = _match_from_extracted_data(extracted)
            if candidate:
                result = _process_candidate_match(
                    candidate, CVUpload.MatchMethod.CV_CONTENT, True,  # needs_review
                    source, file_name, file_content,
                )
                if result:
                    logger.info(
                        "CV matched P5 (CV content): candidate=%s extracted=%s applications=%s",
                        candidate.pk, extracted, result["application_pks"],
                    )
                    return result
        except CVExtractionError as exc:
            logger.warning("P5 CV extraction failed: %s", exc)

    # ── Priority 6: No match — save to UnmatchedInbound ────────────────────────
    unmatched = _save_unmatched(
        channel=channel,
        sender=sender,
        subject=subject,
        text_body=text_body,
        file_name=file_name,
        raw_payload=raw_payload or {},
    )
    logger.info(
        "CV unmatched: sender=%s file=%s → UnmatchedInbound=%s",
        sender, file_name, unmatched.pk,
    )
    return {
        "matched": False,
        "confidence": None,
        "method": None,
        "application_pks": [],
        "cv_upload_pks": [],
        "unmatched_pk": unmatched.pk,
    }


# ─────────────────────────────────────────────────────────────────────────────
# Matching helpers
# ─────────────────────────────────────────────────────────────────────────────

def _match_by_email(email: str) -> Candidate | None:
    """Return a Candidate whose email exactly matches (case-insensitive)."""
    return (
        Candidate.objects
        .filter(email__iexact=email.strip())
        .first()
    )


def _match_by_phone(raw_phone: str) -> Candidate | None:
    """
    Return a Candidate whose phone or whatsapp_number matches.
    Normalises both the query and stored values to digits-only for comparison,
    so +44 7700 900123 matches 07700900123 etc.
    """
    digits = _digits_only(raw_phone)
    if not digits:
        return None

    # Fetch candidates whose stored phone/whatsapp digits contain or equal the
    # submitted digits.  Exact-suffix match handles country-code differences.
    candidates = Candidate.objects.filter(phone__isnull=False).only(
        "id", "phone", "whatsapp_number"
    )
    for candidate in candidates:
        if _phones_match(digits, candidate.phone):
            return candidate
        if candidate.whatsapp_number and _phones_match(digits, candidate.whatsapp_number):
            return candidate
    return None


def _extract_application_id(subject: str, text_body: str) -> int | None:
    """
    Search subject and body for an embedded application reference number.
    Returns the integer PK if found, or None.
    """
    for text in (subject or "", text_body or ""):
        match = _APP_ID_RE.search(text)
        if match:
            try:
                return int(match.group(1))
            except ValueError:
                pass
    return None


def _extract_sender_name(sender: str) -> str | None:
    """
    Parse a display name from an email address string.

    "John Doe <john@example.com>"  → "John Doe"
    "john@example.com"             → None   (no display name)
    "+1234567890"                  → None   (phone; no name to parse)
    """
    match = re.match(r"^([^<@\n]+?)\s*<[^>]+>", sender.strip())
    if match:
        name = match.group(1).strip().strip('"').strip("'")
        if name and len(name) >= 3:
            return name
    return None


def _fuzzy_match_name(name: str, candidates) -> Candidate | None:
    """
    Return the best-matching Candidate whose full_name (or first+last) has a
    SequenceMatcher ratio >= FUZZY_NAME_THRESHOLD against `name`.
    """
    name_lower = name.lower().strip()
    best_candidate = None
    best_ratio = FUZZY_NAME_THRESHOLD - 0.001  # must beat threshold, not just equal

    for candidate in candidates:
        full = build_full_name(candidate.first_name, candidate.last_name).lower()
        ratio = SequenceMatcher(None, name_lower, full).ratio()
        if ratio > best_ratio:
            best_ratio = ratio
            best_candidate = candidate

    return best_candidate


def _match_from_extracted_data(extracted: dict) -> Candidate | None:
    """
    Try to match a candidate from Claude Haiku's extracted CV data.
    Searches only within the awaiting-CV candidate pool.

    Match order (within P5):
      a) Exact email match (extracted email → Candidate.email)
      b) Exact phone match (extracted phone → Candidate.phone)
      c) Fuzzy name match  (extracted first+last → Candidate full name)
    """
    pool = _get_candidates_awaiting_cv()

    # 5a: extracted email
    email = extracted.get("email")
    if email:
        candidate = pool.filter(email__iexact=email.strip()).first()
        if candidate:
            return candidate

    # 5b: extracted phone
    phone = extracted.get("phone")
    if phone:
        digits = _digits_only(phone)
        if digits:
            for candidate in pool.only("id", "phone", "whatsapp_number"):
                if _phones_match(digits, candidate.phone):
                    return candidate
                if candidate.whatsapp_number and _phones_match(digits, candidate.whatsapp_number):
                    return candidate

    # 5c: fuzzy name
    first = extracted.get("first_name") or ""
    last = extracted.get("last_name") or ""
    full_name = f"{first} {last}".strip()
    if full_name:
        return _fuzzy_match_name(full_name, pool)

    return None


def _get_candidates_awaiting_cv():
    """
    Return a QuerySet of Candidates who have at least one Application in an
    awaiting-CV status.  This is the search pool for fuzzy matching (P4, P5).
    """
    return (
        Candidate.objects
        .filter(applications__status__in=list(AWAITING_CV_STATUSES))
        .distinct()
    )


# ─────────────────────────────────────────────────────────────────────────────
# Attachment helpers
# ─────────────────────────────────────────────────────────────────────────────

def _process_candidate_match(
    candidate: Candidate,
    match_method: str,
    needs_review: bool,
    source: str,
    file_name: str,
    file_content: bytes,
) -> dict | None:
    """
    Find all awaiting-CV applications for the candidate, save the CV file,
    create CVUpload records, and advance each application's status.

    Returns a result dict if any applications were updated, or None if the
    candidate has no awaiting-CV applications (caller should try next priority).
    """
    applications = list(
        Application.objects
        .filter(
            candidate=candidate,
            status__in=list(AWAITING_CV_STATUSES),
        )
        .select_related("candidate", "position")
    )

    if not applications:
        logger.debug(
            "Candidate %s matched but has no awaiting-CV applications — falling through",
            candidate.pk,
        )
        return None

    file_path = _save_cv_file(file_name, file_content)
    cv_upload_pks = []
    app_pks = []

    with transaction.atomic():
        for app in applications:
            cv = CVUpload.objects.create(
                application=app,
                file_name=file_name,
                file_path=file_path,
                source=source,
                match_method=match_method,
                needs_review=needs_review,
            )
            cv_upload_pks.append(cv.pk)
            app_pks.append(app.pk)
            advance_application_status(app)

    confidence = "medium" if needs_review else "high"
    return {
        "matched": True,
        "confidence": confidence,
        "method": match_method,
        "application_pks": app_pks,
        "cv_upload_pks": cv_upload_pks,
        "unmatched_pk": None,
    }


# ─────────────────────────────────────────────────────────────────────────────
# File I/O
# ─────────────────────────────────────────────────────────────────────────────

def _save_cv_file(file_name: str, file_content: bytes) -> str:
    """
    Persist CV bytes under MEDIA_ROOT/cvs/ with a UUID-prefixed name to prevent
    collisions.  Returns the path relative to MEDIA_ROOT.
    """
    cv_dir = Path(settings.MEDIA_ROOT) / "cvs"
    cv_dir.mkdir(parents=True, exist_ok=True)

    # Sanitise the original filename before embedding in the path
    safe_name = re.sub(r"[^\w\-.]", "_", file_name)[:200]
    unique_name = f"{uuid.uuid4().hex}_{safe_name}"
    abs_path = cv_dir / unique_name
    abs_path.write_bytes(file_content)

    relative_path = str(Path("cvs") / unique_name)
    logger.debug("CV file saved: %s (%d bytes)", relative_path, len(file_content))
    return relative_path


def _extract_text_from_file(file_name: str, file_content: bytes) -> str:
    """
    Extract plain text from a CV file for P5 content analysis.

    Supports:
      - PDF  (via pdfplumber, first PDF_MAX_PAGES pages)
      - Other formats  (UTF-8 decode with error replacement)
    """
    name_lower = (file_name or "").lower()

    if name_lower.endswith(".pdf"):
        return _extract_pdf_text(file_content)

    # Fallback: treat as plain text (e.g. .txt, .docx raw bytes are discardable)
    try:
        return file_content.decode("utf-8", errors="replace")[:8000]
    except Exception:
        return ""


def _extract_pdf_text(content: bytes) -> str:
    """
    Extract text from the first PDF_MAX_PAGES pages using pdfplumber.
    Returns empty string on any extraction error.
    """
    try:
        with pdfplumber.open(io.BytesIO(content)) as pdf:
            pages = pdf.pages[:PDF_MAX_PAGES]
            parts = []
            for page in pages:
                text = page.extract_text()
                if text:
                    parts.append(text)
            return "\n".join(parts)
    except Exception as exc:
        logger.warning("pdfplumber extraction failed for CV content: %s", exc)
        return ""


# ─────────────────────────────────────────────────────────────────────────────
# Unmatched inbound
# ─────────────────────────────────────────────────────────────────────────────

def _save_unmatched(
    channel: str,
    sender: str,
    subject: str,
    text_body: str,
    file_name: str,
    raw_payload: dict,
) -> UnmatchedInbound:
    """Create an UnmatchedInbound record for manual recruiter assignment."""
    return UnmatchedInbound.objects.create(
        channel=channel,
        sender=sender,
        subject=(subject or None),
        body_snippet=(text_body or "")[:500] or None,
        attachment_name=file_name or None,
        raw_payload=raw_payload,
    )


# ─────────────────────────────────────────────────────────────────────────────
# Phone normalisation
# ─────────────────────────────────────────────────────────────────────────────

def _digits_only(phone: str) -> str:
    """Strip all non-digit characters from a phone string."""
    return re.sub(r"\D", "", phone or "")


def _phones_match(query_digits: str, stored_phone: str) -> bool:
    """
    Compare two phone numbers by their digit-only representations.
    Handles country-code prefix differences by checking if either is a suffix
    of the other (minimum 7 significant digits required).

    Examples:
      "+44 7700 900123" vs "07700900123"  → True (suffix match)
      "7700900123"      vs "+447700900123"→ True (suffix match)
      "12345"           vs "12345"        → True (exact match)
    """
    stored_digits = _digits_only(stored_phone)
    if not stored_digits or len(query_digits) < 7:
        return False

    # Exact match
    if query_digits == stored_digits:
        return True

    # Suffix match: the shorter number's digits are the tail of the longer
    short, long_ = (
        (query_digits, stored_digits)
        if len(query_digits) <= len(stored_digits)
        else (stored_digits, query_digits)
    )
    return long_.endswith(short) and len(short) >= 7


# ─────────────────────────────────────────────────────────────────────────────
# Misc helpers
# ─────────────────────────────────────────────────────────────────────────────

