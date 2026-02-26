"""
candidates/tests.py

Covers:
  - _clean_phone         : phone number normalisation
  - _parse_full_name     : name splitting
  - lookup_candidate_by_phone : shared phone lookup helper (§13.8)
  - lookup_candidate_by_email : shared email lookup helper (§13.8)
  - import_meta_csv      : CSV import service (§5)
"""

import io

from django.test import TestCase

from candidates.models import Candidate
from candidates.services import (
    _clean_phone,
    _parse_full_name,
    lookup_candidate_by_email,
    lookup_candidate_by_phone,
    import_meta_csv,
)
from positions.models import Position


# ── Helpers ────────────────────────────────────────────────────────────────────

def _make_position(**kwargs) -> Position:
    defaults = dict(
        title="Sales Rep",
        description="Great role",
        campaign_questions="Q1\nQ2",
    )
    defaults.update(kwargs)
    return Position.objects.create(**defaults)


def _build_csv(rows: list[dict]) -> io.BytesIO:
    """
    Build a minimal Meta-style UTF-16 LE tab-delimited CSV in-memory.

    The first row in `rows` must contain all column headers as keys.
    """
    headers = list(rows[0].keys())
    lines = ["\t".join(headers)]
    for row in rows:
        lines.append("\t".join(str(row.get(h, "")) for h in headers))
    text = "\n".join(lines)
    buf = io.BytesIO(text.encode("utf-16"))
    buf.seek(0)
    return buf


# ── _clean_phone ───────────────────────────────────────────────────────────────

class CleanPhoneTests(TestCase):
    def test_strips_p_prefix(self):
        self.assertEqual(_clean_phone("p:+40712345678"), "+40712345678")

    def test_strips_p_prefix_case_insensitive(self):
        self.assertEqual(_clean_phone("P:+40712345678"), "+40712345678")

    def test_removes_spaces(self):
        self.assertEqual(_clean_phone("+40 712 345 678"), "+40712345678")

    def test_removes_dashes(self):
        self.assertEqual(_clean_phone("+40-712-345-678"), "+40712345678")

    def test_preserves_leading_plus(self):
        result = _clean_phone("+40712345678")
        self.assertTrue(result.startswith("+"))

    def test_strips_p_prefix_then_removes_spaces(self):
        self.assertEqual(_clean_phone("p:+40 712 345 678"), "+40712345678")


# ── _parse_full_name ───────────────────────────────────────────────────────────

class ParseFullNameTests(TestCase):
    def test_normal_first_last(self):
        first, last = _parse_full_name("Ana Pop")
        self.assertEqual(first, "Ana")
        self.assertEqual(last, "Pop")

    def test_multi_word_last_name(self):
        first, last = _parse_full_name("Maria Elena Popescu")
        self.assertEqual(first, "Maria")
        self.assertEqual(last, "Elena Popescu")

    def test_single_word_name(self):
        first, last = _parse_full_name("Madonna")
        self.assertEqual(first, "Madonna")
        self.assertEqual(last, "")

    def test_leading_trailing_whitespace(self):
        # Outer whitespace is stripped; single internal space produces clean last name.
        first, last = _parse_full_name("  Ion Ionescu  ")
        self.assertEqual(first, "Ion")
        self.assertEqual(last, "Ionescu")


# ── lookup_candidate_by_phone ──────────────────────────────────────────────────

class LookupCandidateByPhoneTests(TestCase):
    def setUp(self):
        self.candidate = Candidate.objects.create(
            first_name="Ana",
            last_name="Pop",
            full_name="Ana Pop",
            phone="+40712345678",
            email="ana@example.com",
        )

    def test_exact_match(self):
        result = lookup_candidate_by_phone("+40712345678")
        self.assertEqual(result, self.candidate)

    def test_match_without_country_code(self):
        # 712345678 is a suffix of +40712345678
        result = lookup_candidate_by_phone("712345678")
        self.assertEqual(result, self.candidate)

    def test_no_match_returns_none(self):
        result = lookup_candidate_by_phone("+40999999999")
        self.assertIsNone(result)

    def test_too_short_phone_returns_none(self):
        result = lookup_candidate_by_phone("123")
        self.assertIsNone(result)

    def test_empty_string_returns_none(self):
        result = lookup_candidate_by_phone("")
        self.assertIsNone(result)

    def test_match_via_whatsapp_number(self):
        candidate = Candidate.objects.create(
            first_name="Ion",
            last_name="Ionescu",
            full_name="Ion Ionescu",
            phone="+40700000002",
            email="ion@example.com",
            whatsapp_number="+40711222333",
        )
        result = lookup_candidate_by_phone("+40711222333")
        self.assertEqual(result, candidate)


# ── lookup_candidate_by_email ──────────────────────────────────────────────────

class LookupCandidateByEmailTests(TestCase):
    def setUp(self):
        self.candidate = Candidate.objects.create(
            first_name="Ana",
            last_name="Pop",
            full_name="Ana Pop",
            phone="+40712345678",
            email="ana@example.com",
        )

    def test_exact_match(self):
        result = lookup_candidate_by_email("ana@example.com")
        self.assertEqual(result, self.candidate)

    def test_case_insensitive_match(self):
        result = lookup_candidate_by_email("ANA@EXAMPLE.COM")
        self.assertEqual(result, self.candidate)

    def test_rfc2822_name_plus_address_format(self):
        result = lookup_candidate_by_email("Ana Pop <ana@example.com>")
        self.assertEqual(result, self.candidate)

    def test_no_match_returns_none(self):
        result = lookup_candidate_by_email("nobody@example.com")
        self.assertIsNone(result)

    def test_empty_string_returns_none(self):
        result = lookup_candidate_by_email("")
        self.assertIsNone(result)

    def test_invalid_email_without_at_returns_none(self):
        result = lookup_candidate_by_email("notanemail")
        self.assertIsNone(result)


# ── import_meta_csv ────────────────────────────────────────────────────────────

class ImportMetaCSVTests(TestCase):
    def setUp(self):
        self.position = _make_position()

    def test_creates_candidate_and_application(self):
        csv_file = _build_csv([{
            "id": "l:1111",
            "created_time": "2024-01-01T10:00:00+0000",
            "campaign_name": "Sales Campaign",
            "platform": "fb",
            "email": "new@example.com",
            "full_name": "New Candidate",
            "phone_number": "+40700111222",
        }])

        summary = import_meta_csv(csv_file, self.position.pk)

        self.assertEqual(summary["created"], 1)
        self.assertEqual(summary["updated"], 0)
        self.assertEqual(summary["applications_created"], 1)
        self.assertEqual(summary["applications_skipped"], 0)
        self.assertEqual(len(summary["errors"]), 0)

        candidate = Candidate.objects.get(meta_lead_id="l:1111")
        self.assertEqual(candidate.first_name, "New")
        self.assertEqual(candidate.last_name, "Candidate")
        self.assertEqual(candidate.email, "new@example.com")
        self.assertEqual(candidate.phone, "+40700111222")

    def test_upserts_existing_candidate_by_meta_lead_id(self):
        # Create an existing candidate with the same meta_lead_id
        Candidate.objects.create(
            first_name="Old",
            last_name="Name",
            full_name="Old Name",
            phone="+40700111222",
            email="old@example.com",
            meta_lead_id="l:2222",
            source=Candidate.Source.META_FORM,
        )

        csv_file = _build_csv([{
            "id": "l:2222",
            "created_time": "2024-01-01T10:00:00+0000",
            "campaign_name": "Sales Campaign",
            "platform": "fb",
            "email": "updated@example.com",
            "full_name": "Updated Name",
            "phone_number": "+40700111222",
        }])

        summary = import_meta_csv(csv_file, self.position.pk)

        self.assertEqual(summary["created"], 0)
        self.assertEqual(summary["updated"], 1)

        candidate = Candidate.objects.get(meta_lead_id="l:2222")
        self.assertEqual(candidate.email, "updated@example.com")

    def test_skips_duplicate_application(self):
        from applications.models import Application
        candidate = Candidate.objects.create(
            first_name="Ana",
            last_name="Pop",
            full_name="Ana Pop",
            phone="+40700000001",
            email="ana@example.com",
            meta_lead_id="l:3333",
            source=Candidate.Source.META_FORM,
        )
        Application.objects.create(
            candidate=candidate,
            position=self.position,
        )

        csv_file = _build_csv([{
            "id": "l:3333",
            "created_time": "2024-01-01T10:00:00+0000",
            "campaign_name": "Campaign",
            "platform": "fb",
            "email": "ana@example.com",
            "full_name": "Ana Pop",
            "phone_number": "+40700000001",
        }])

        summary = import_meta_csv(csv_file, self.position.pk)

        self.assertEqual(summary["applications_skipped"], 1)
        self.assertEqual(summary["applications_created"], 0)

    def test_detects_potential_duplicate_by_phone(self):
        # A pre-existing candidate with the same phone but a different meta_lead_id
        Candidate.objects.create(
            first_name="Existing",
            last_name="Person",
            full_name="Existing Person",
            phone="+40700999888",
            email="existing@example.com",
            meta_lead_id="l:existing",
            source=Candidate.Source.META_FORM,
        )

        csv_file = _build_csv([{
            "id": "l:newlead",
            "created_time": "2024-01-01T10:00:00+0000",
            "campaign_name": "Campaign",
            "platform": "fb",
            "email": "different@example.com",
            "full_name": "New Person",
            "phone_number": "+40700999888",
        }])

        summary = import_meta_csv(csv_file, self.position.pk)

        self.assertEqual(len(summary["potential_duplicates"]), 1)
        self.assertEqual(summary["potential_duplicates"][0]["match_reason"], "phone")

    def test_strips_p_prefix_from_phone(self):
        csv_file = _build_csv([{
            "id": "l:4444",
            "created_time": "2024-01-01T10:00:00+0000",
            "campaign_name": "Campaign",
            "platform": "fb",
            "email": "pphone@example.com",
            "full_name": "P Phone",
            "phone_number": "p:+40712345000",
        }])

        import_meta_csv(csv_file, self.position.pk)

        candidate = Candidate.objects.get(meta_lead_id="l:4444")
        self.assertEqual(candidate.phone, "+40712345000")

    def test_stores_dynamic_form_answers_as_json(self):
        csv_file = _build_csv([{
            "id": "l:5555",
            "created_time": "2024-01-01T10:00:00+0000",
            "campaign_name": "Campaign",
            "platform": "fb",
            "email": "dyn@example.com",
            "full_name": "Dynamic Form",
            "phone_number": "+40712345001",
            "do_you_have_a_drivers_license": "yes",
            "available_for_night_shifts": "no",
        }])

        import_meta_csv(csv_file, self.position.pk)

        candidate = Candidate.objects.get(meta_lead_id="l:5555")
        self.assertIsNotNone(candidate.form_answers)
        self.assertIn("do_you_have_a_drivers_license", candidate.form_answers)

    def test_raises_value_error_for_invalid_position(self):
        csv_file = _build_csv([{
            "id": "l:x",
            "created_time": "",
            "campaign_name": "",
            "platform": "",
            "email": "x@x.com",
            "full_name": "X X",
            "phone_number": "+40700000000",
        }])
        with self.assertRaises(ValueError):
            import_meta_csv(csv_file, position_id=99999)
