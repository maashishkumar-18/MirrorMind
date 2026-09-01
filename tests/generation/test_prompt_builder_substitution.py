"""
Characterization tests for PromptBuilder._substitute_with_validation
(src/generation/prompt_builder.py:803-833).

Pure string function -- only needs self.log_missing_variables, so
PromptBuilder is constructed via __new__() rather than its real __init__,
which eagerly constructs a TemplateLoader that validates a real templates
directory exists (src/generation/prompt_builder.py:527-618) -- unrelated
to what's under test here.
"""

import logging

import pytest

from src.generation.prompt_builder import PromptBuilder

pytestmark = pytest.mark.characterization


def _make_builder(log_missing_variables=True):
    builder = PromptBuilder.__new__(PromptBuilder)
    builder.log_missing_variables = log_missing_variables
    return builder


class TestSubstituteWithValidation:
    def test_all_placeholders_present(self):
        builder = _make_builder()
        result = builder._substitute_with_validation(
            "Hello {name}, you have {count} items.", {"name": "Alice", "count": "3"}
        )
        assert result == "Hello Alice, you have 3 items."

    def test_missing_placeholder_left_verbatim_in_output(self, caplog):
        builder = _make_builder()
        with caplog.at_level(logging.WARNING):
            result = builder._substitute_with_validation(
                "Hello {name}, {missing} here.", {"name": "Bob"}
            )
        assert result == "Hello Bob, {missing} here."
        assert "Missing template variable" in caplog.text
        assert "{missing}" in caplog.text

    def test_log_missing_variables_false_suppresses_warning_but_same_substitution(self, caplog):
        builder = _make_builder(log_missing_variables=False)
        with caplog.at_level(logging.WARNING):
            result = builder._substitute_with_validation("Hi {missing}", {})
        assert result == "Hi {missing}"
        assert caplog.text == ""

    def test_no_placeholders_returns_template_unchanged(self):
        builder = _make_builder()
        result = builder._substitute_with_validation("No placeholders here.", {"unused": "x"})
        assert result == "No placeholders here."

    def test_duplicate_placeholder_all_occurrences_replaced(self):
        builder = _make_builder()
        result = builder._substitute_with_validation("{x} and {x} again", {"x": "Y"})
        assert result == "Y and Y again"

    def test_extra_variables_not_referenced_in_template_are_ignored(self):
        builder = _make_builder()
        result = builder._substitute_with_validation("Just {a}.", {"a": "1", "b": "2", "c": "3"})
        assert result == "Just 1."

    def test_uses_str_replace_not_str_format_stray_braces_are_safe(self):
        """A literal `{` with no matching placeholder pattern (e.g. from a
        JSON example embedded in the template) must not raise -- str.format
        would raise KeyError/IndexError here, str.replace does not."""
        builder = _make_builder()
        template = 'Example: {"key": "value"} and {name}'
        result = builder._substitute_with_validation(template, {"name": "Alice"})
        assert result == 'Example: {"key": "value"} and Alice'

    def test_empty_variables_dict_leaves_all_placeholders(self, caplog):
        builder = _make_builder()
        with caplog.at_level(logging.WARNING):
            result = builder._substitute_with_validation("{a} {b}", {})
        assert result == "{a} {b}"
