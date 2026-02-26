"""
prompts/tests.py

Covers:
  - PromptTemplate model creation and defaults (§4.10)
  - Per-section active logic described in §13.10
"""

from django.test import TestCase

from prompts.models import PromptTemplate


class PromptTemplateModelTests(TestCase):
    def test_create_template_with_section(self):
        tpl = PromptTemplate.objects.create(
            section=PromptTemplate.Section.SYSTEM_PROMPT,
            name="System Prompt v1",
            meta_prompt="Generate a system prompt for {title}.",
        )
        self.assertEqual(tpl.section, PromptTemplate.Section.SYSTEM_PROMPT)
        self.assertEqual(tpl.version, 1)
        self.assertFalse(tpl.is_active)

    def test_default_version_is_1(self):
        tpl = PromptTemplate.objects.create(
            section=PromptTemplate.Section.FIRST_MESSAGE,
            name="First Message v1",
            meta_prompt="Generate a first message.",
        )
        self.assertEqual(tpl.version, 1)

    def test_default_is_active_false(self):
        tpl = PromptTemplate.objects.create(
            section=PromptTemplate.Section.QUALIFICATION_PROMPT,
            name="Qualification v1",
            meta_prompt="Evaluate the candidate.",
        )
        self.assertFalse(tpl.is_active)

    def test_create_template_without_section_allowed(self):
        """Legacy templates may have section=None."""
        tpl = PromptTemplate.objects.create(
            section=None,
            name="Legacy Template",
            meta_prompt="Old-style meta prompt.",
        )
        self.assertIsNone(tpl.section)

    def test_str_includes_section_and_name(self):
        tpl = PromptTemplate.objects.create(
            section=PromptTemplate.Section.SYSTEM_PROMPT,
            name="System v2",
            meta_prompt="...",
            is_active=True,
        )
        result = str(tpl)
        self.assertIn("System Prompt", result)
        self.assertIn("System v2", result)

    def test_str_for_legacy_null_section(self):
        tpl = PromptTemplate.objects.create(
            section=None,
            name="Old Template",
            meta_prompt="...",
        )
        result = str(tpl)
        self.assertIn("Old Template", result)

    def test_activate_template_per_section_independently(self):
        """
        Activating a template for one section should not affect templates
        from other sections. The DB model itself has no constraint — the
        enforcement is done in ToggleActiveView (spec §13.10).  This test
        validates that two templates from different sections can both be
        active simultaneously (i.e. no DB-level uniqueness violation).
        """
        tpl_system = PromptTemplate.objects.create(
            section=PromptTemplate.Section.SYSTEM_PROMPT,
            name="System Active",
            meta_prompt="...",
            is_active=True,
        )
        tpl_first_msg = PromptTemplate.objects.create(
            section=PromptTemplate.Section.FIRST_MESSAGE,
            name="First Msg Active",
            meta_prompt="...",
            is_active=True,
        )
        self.assertTrue(tpl_system.is_active)
        self.assertTrue(tpl_first_msg.is_active)
