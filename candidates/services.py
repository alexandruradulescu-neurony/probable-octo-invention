"""
candidates/services.py

Meta Ads Lead Form CSV import service.

Spec reference: Section 5 — CSV Import Specification
  Encoding  : UTF-16 LE (with BOM)
  Delimiter : Tab (\t)
  Source    : Meta Ads Manager → Lead Ads export
"""

import csv
import io
import logging
import re

from django.db import transaction
from django.db.models import Q
from django.utils.dateparse import parse_datetime
from django.utils import timezone

from candidates.models import Candidate
from applications.models import Application
from positions.models import Position

logger = logging.getLogger(__name__)

# ── Column classification ──────────────────────────────────────────────────────

# Columns that map directly to Candidate fields (handled explicitly).
STANDARD_COLUMNS = frozenset({
    "id",
    "created_time",
    "campaign_name",
    "platform",
    "email",
    "full_name",
    "phone_number",
})

# Columns present in Meta exports that carry no useful candidate data.
IGNORED_COLUMNS = frozenset({
    "ad_id",
    "ad_name",
    "adset_id",
    "adset_name",
    "form_id",
    "form_name",
    "is_organic",
    "inbox_url",
})


# ── Field transformations ──────────────────────────────────────────────────────

def _clean_phone(raw: str) -> str:
    """
    Strip the 'p:' prefix Meta adds to phone numbers, then keep only
    the leading '+' and digits.

    Examples:
        'p:+40712345678'  →  '+40712345678'
        '+40 712 345 678' →  '+40712345678'
    """
    phone = raw.strip()
    if phone.lower().startswith("p:"):
        phone = phone[2:]
    return re.sub(r"[^\d+]", "", phone)


def _parse_full_name(full_name: str) -> tuple[str, str]:
    """
    Split 'First Last Name' into ('First', 'Last Name').
    If only one word is present, last_name is left empty.
    """
    parts = full_name.strip().split(" ", 1)
    first = parts[0] if parts else ""
    last = parts[1] if len(parts) > 1 else ""
    return first, last


def _parse_meta_datetime(raw: str):
    """
    Parse Meta's created_time field (ISO 8601, possibly with space separator).
    Returns an aware datetime or None.

    Meta formats seen in the wild:
        '2024-08-15T10:30:00+0000'
        '2024-08-15 10:30:00+00:00'
    """
    if not raw:
        return None
    # Normalise space-separated format to 'T' separator for parse_datetime
    normalised = raw.strip().replace(" ", "T", 1)
    dt = parse_datetime(normalised)
    if dt is not None and timezone.is_naive(dt):
        dt = timezone.make_aware(dt, timezone.utc)
    return dt


def _clean_form_value(value: str) -> str:
    """
    Clean a dynamic form answer value:
    Meta sometimes URL-encodes spaces as underscores in exported values.
    """
    return value.replace("_", " ").strip()


# ── Public API ─────────────────────────────────────────────────────────────────

def import_meta_csv(file_path_or_object, position_id: int) -> dict:
    """
    Import a Meta Ads Lead Form CSV export.

    Args:
        file_path_or_object: A file-system path (str | Path) or an open
                             file-like object (e.g. Django's InMemoryUploadedFile).
        position_id:         PK of the Position to link all imported candidates to.

    Returns:
        A summary dict::

            {
                "total_rows":            int,
                "created":               int,   # new Candidates created
                "updated":               int,   # existing Candidates updated
                "applications_created":  int,
                "applications_skipped":  int,   # already existed
                "potential_duplicates":  list[dict],
                "errors":                list[str],
            }

    Raises:
        ValueError: if the Position does not exist.
        FileNotFoundError: if a file path is given and the file is missing.
    """
    try:
        position = Position.objects.get(pk=position_id)
    except Position.DoesNotExist:
        raise ValueError(f"Position with id={position_id} does not exist.")

    rows = _read_csv(file_path_or_object)

    summary = {
        "total_rows": len(rows),
        "created": 0,
        "updated": 0,
        "applications_created": 0,
        "applications_skipped": 0,
        "potential_duplicates": [],
        "errors": [],
    }

    for row_num, row in enumerate(rows, start=2):  # row 1 is the header
        try:
            with transaction.atomic():
                _process_row(row, position, summary)
        except Exception as exc:
            msg = f"Row {row_num}: {exc}"
            logger.warning(msg, exc_info=True)
            summary["errors"].append(msg)

    return summary


# ── Internal helpers ───────────────────────────────────────────────────────────

def _read_csv(file_path_or_object) -> list[dict]:
    """
    Read and decode the Meta CSV (UTF-16 LE with BOM, tab-delimited).
    Returns a list of row dicts.
    """
    if hasattr(file_path_or_object, "read"):
        raw = file_path_or_object.read()
        # Accept both bytes (uploaded file) and pre-decoded strings.
        if isinstance(raw, bytes):
            text = raw.decode("utf-16")
        else:
            text = raw
    else:
        with open(file_path_or_object, encoding="utf-16") as fh:
            text = fh.read()

    reader = csv.DictReader(io.StringIO(text), delimiter="\t")
    return list(reader)


def _process_row(row: dict, position: Position, summary: dict) -> None:
    """
    Process one CSV row:
      1. Parse and clean all fields.
      2. Upsert the Candidate (keyed on meta_lead_id).
      3. Create the Application if it doesn't exist yet.
    """
    meta_lead_id = (row.get("id") or "").strip() or None

    full_name = (row.get("full_name") or "").strip()
    first_name, last_name = _parse_full_name(full_name)

    email = (row.get("email") or "").strip().lower()
    phone = _clean_phone(row.get("phone_number") or "")
    campaign_name = (row.get("campaign_name") or "").strip() or None
    platform = (row.get("platform") or "").strip() or None
    meta_created_time = _parse_meta_datetime(row.get("created_time") or "")

    # Collect dynamic form question columns
    form_answers = {
        col.strip(): _clean_form_value(val)
        for col, val in row.items()
        if col.strip() not in STANDARD_COLUMNS
        and col.strip() not in IGNORED_COLUMNS
        and (val or "").strip()
    }

    candidate_data = {
        "first_name": first_name,
        "last_name": last_name,
        "full_name": full_name,
        "phone": phone,
        "email": email,
        "meta_created_time": meta_created_time,
        "campaign_name": campaign_name,
        "platform": platform,
        "form_answers": form_answers if form_answers else None,
        "source": Candidate.Source.META_FORM,
    }

    if meta_lead_id:
        candidate, created = Candidate.objects.update_or_create(
            meta_lead_id=meta_lead_id,
            defaults=candidate_data,
        )
        if created:
            summary["created"] += 1
            _check_for_duplicates(candidate, phone, email, summary)
        else:
            summary["updated"] += 1
    else:
        # No meta_lead_id — shouldn't happen with real Meta exports but handle gracefully.
        candidate = Candidate.objects.create(meta_lead_id=None, **candidate_data)
        summary["created"] += 1

    # Create Application only if this candidate+position pair doesn't exist yet.
    _, app_created = Application.objects.get_or_create(
        candidate=candidate,
        position=position,
        defaults={"status": Application.Status.PENDING_CALL},
    )
    if app_created:
        summary["applications_created"] += 1
    else:
        summary["applications_skipped"] += 1


def _check_for_duplicates(
    new_candidate: Candidate,
    phone: str,
    email: str,
    summary: dict,
) -> None:
    """
    Secondary deduplication check (spec §5 — Deduplication).

    If a newly created candidate's phone or email matches an existing
    candidate (different meta_lead_id), flag it for recruiter review.
    """
    if not phone and not email:
        return

    query = Q()
    if phone:
        query |= Q(phone=phone)
    if email:
        query |= Q(email=email)

    duplicates = (
        Candidate.objects.filter(query)
        .exclude(pk=new_candidate.pk)
        .only("pk", "meta_lead_id", "phone", "email")
    )

    for dup in duplicates:
        match_reason = "phone" if dup.phone == phone else "email"
        summary["potential_duplicates"].append({
            "new_candidate_id": new_candidate.pk,
            "new_meta_lead_id": new_candidate.meta_lead_id,
            "matching_candidate_id": dup.pk,
            "matching_meta_lead_id": dup.meta_lead_id,
            "match_reason": match_reason,
        })
        logger.info(
            "Potential duplicate: new candidate #%s matches existing #%s by %s",
            new_candidate.pk,
            dup.pk,
            match_reason,
        )
