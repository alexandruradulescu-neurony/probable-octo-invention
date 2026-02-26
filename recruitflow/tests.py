"""
recruitflow/tests.py

Covers:
  - text_utils.strip_json_fence     (§13.3)
  - text_utils.build_full_name      (§13.3)
  - text_utils.humanize_form_question (§13.3)
"""

from django.test import TestCase

from recruitflow.text_utils import (
    build_full_name,
    humanize_form_question,
    strip_json_fence,
)


class StripJsonFenceTests(TestCase):
    def test_strips_json_fenced_block(self):
        raw = '```json\n{"key": "value"}\n```'
        result = strip_json_fence(raw)
        self.assertEqual(result, '{"key": "value"}')

    def test_strips_plain_fenced_block(self):
        raw = '```\n{"key": "value"}\n```'
        result = strip_json_fence(raw)
        self.assertEqual(result, '{"key": "value"}')

    def test_no_fence_returns_as_is(self):
        raw = '{"key": "value"}'
        result = strip_json_fence(raw)
        self.assertEqual(result, '{"key": "value"}')

    def test_empty_string_returns_empty(self):
        self.assertEqual(strip_json_fence(""), "")

    def test_none_equivalent_handled(self):
        # strip_json_fence uses (raw or "").strip() so None-like empty input is safe
        self.assertEqual(strip_json_fence(""), "")

    def test_strips_multiline_json_fence(self):
        raw = '```json\n{\n  "qualified": true,\n  "score": 90\n}\n```'
        result = strip_json_fence(raw)
        self.assertIn('"qualified": true', result)
        self.assertNotIn("```", result)

    def test_case_insensitive_json_tag(self):
        raw = '```JSON\n{"a": 1}\n```'
        result = strip_json_fence(raw)
        self.assertEqual(result, '{"a": 1}')


class BuildFullNameTests(TestCase):
    def test_both_names(self):
        self.assertEqual(build_full_name("Ana", "Pop"), "Ana Pop")

    def test_first_name_only(self):
        self.assertEqual(build_full_name("Madonna", ""), "Madonna")

    def test_last_name_only(self):
        self.assertEqual(build_full_name("", "Pop"), "Pop")

    def test_both_empty_returns_empty(self):
        self.assertEqual(build_full_name("", ""), "")

    def test_none_first_name(self):
        result = build_full_name(None, "Pop")
        self.assertEqual(result, "Pop")

    def test_none_last_name(self):
        result = build_full_name("Ana", None)
        self.assertEqual(result, "Ana")

    def test_none_both(self):
        result = build_full_name(None, None)
        self.assertEqual(result, "")


class HumanizeFormQuestionTests(TestCase):
    def test_underscores_replaced_by_spaces(self):
        result = humanize_form_question("do_you_have_a_drivers_license")
        self.assertNotIn("_", result)
        self.assertIn(" ", result)

    def test_first_letter_capitalised(self):
        result = humanize_form_question("have_you_worked_in_sales")
        self.assertTrue(result[0].isupper())

    def test_empty_string_returns_empty(self):
        self.assertEqual(humanize_form_question(""), "")

    def test_already_readable_unchanged(self):
        result = humanize_form_question("Experience")
        self.assertEqual(result, "Experience")

    def test_leading_trailing_whitespace_stripped(self):
        result = humanize_form_question("  question  ")
        self.assertEqual(result, "Question")
