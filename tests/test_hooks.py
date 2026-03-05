"""Tests for hook script functions.

Uses importlib to load standalone scripts from hooks/ directory.
"""

from __future__ import annotations

import importlib.util
import json
import os
import sys
import tempfile

import pytest

# ---- Import hook modules from file paths ----

_HOOKS_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "hooks"
)


def _import_hook(filename: str, module_name: str):
    """Import a hook script by file path."""
    path = os.path.join(_HOOKS_DIR, filename)
    spec = importlib.util.spec_from_file_location(module_name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


extract_mod = _import_hook("extract-last-message.py", "extract_last_message")
format_mod = _import_hook("format-question.py", "format_question")


# ---- extract-last-message.py tests ----


class TestStripEnglishCoach:
    def test_no_coach_block(self):
        assert extract_mod.strip_english_coach("hello world") == "hello world"

    def test_strips_coach_block(self):
        text = "---\nEnglish Coach content\n---\nActual response"
        result = extract_mod.strip_english_coach(text)
        assert result == "Actual response"

    def test_empty_string(self):
        assert extract_mod.strip_english_coach("") == ""

    def test_only_dashes(self):
        result = extract_mod.strip_english_coach("---\ncontent\n---")
        # After stripping the coach block, nothing remains -> returns original
        assert result is not None

    def test_preserves_later_dashes(self):
        text = "---\ncoach\n---\nreal content---with dashes"
        result = extract_mod.strip_english_coach(text)
        assert "real content---with dashes" in result


class TestTablesToCodeblocks:
    def test_no_table(self):
        text = "hello\nworld"
        assert extract_mod.tables_to_codeblocks(text) == text

    def test_wraps_table(self):
        text = "before\n| col1 | col2 |\n| --- | --- |\n| a | b |\nafter"
        result = extract_mod.tables_to_codeblocks(text)
        lines = result.split("\n")
        assert lines[0] == "before"
        assert lines[1] == "```"
        assert lines[2] == "| col1 | col2 |"
        assert "```" in lines  # closing backtick
        assert lines[-1] == "after"

    def test_table_at_end(self):
        text = "intro\n| a | b |"
        result = extract_mod.tables_to_codeblocks(text)
        assert result.endswith("```")

    def test_empty_string(self):
        assert extract_mod.tables_to_codeblocks("") == ""


class TestExtractLastAssistantText:
    def _write_jsonl(self, tmpdir, lines):
        path = os.path.join(tmpdir, "session.jsonl")
        with open(path, "w") as f:
            for line in lines:
                f.write(json.dumps(line) + "\n")
        return path

    def test_extracts_text(self, tmp_path):
        path = self._write_jsonl(tmp_path, [
            {"type": "assistant", "message": {"content": [
                {"type": "text", "text": "Hello from assistant"}
            ]}},
        ])
        result = extract_mod.extract_last_assistant_text(path)
        assert result == "Hello from assistant"

    def test_returns_none_for_empty_file(self, tmp_path):
        path = self._write_jsonl(tmp_path, [])
        result = extract_mod.extract_last_assistant_text(path)
        assert result is None

    def test_skips_non_assistant(self, tmp_path):
        path = self._write_jsonl(tmp_path, [
            {"type": "user", "message": {"content": [
                {"type": "text", "text": "user message"}
            ]}},
        ])
        result = extract_mod.extract_last_assistant_text(path)
        assert result is None

    def test_truncates_long_content(self, tmp_path):
        long_text = "x" * 2000
        path = self._write_jsonl(tmp_path, [
            {"type": "assistant", "message": {"content": [
                {"type": "text", "text": long_text}
            ]}},
        ])
        result = extract_mod.extract_last_assistant_text(path, max_chars=100)
        assert len(result) == 103  # 100 + "..."
        assert result.endswith("...")

    def test_skips_tool_only_turns(self, tmp_path):
        path = self._write_jsonl(tmp_path, [
            {"type": "assistant", "message": {"content": [
                {"type": "text", "text": "Earlier text"}
            ]}},
            {"type": "assistant", "message": {"content": [
                {"type": "tool_use", "name": "Read", "input": {}}
            ]}},
        ])
        result = extract_mod.extract_last_assistant_text(path)
        assert result == "Earlier text"

    def test_returns_none_for_interactive_tool(self, tmp_path):
        path = self._write_jsonl(tmp_path, [
            {"type": "assistant", "message": {"content": [
                {"type": "text", "text": "Some text"},
                {"type": "tool_use", "name": "AskUserQuestion", "input": {}},
            ]}},
        ])
        result = extract_mod.extract_last_assistant_text(path)
        assert result is None

    def test_handles_malformed_json(self, tmp_path):
        path = os.path.join(tmp_path, "bad.jsonl")
        with open(path, "w") as f:
            f.write("not json\n")
            f.write('{"type": "assistant", "message": {"content": [{"type": "text", "text": "ok"}]}}\n')
        result = extract_mod.extract_last_assistant_text(path)
        assert result == "ok"

    def test_handles_missing_fields(self, tmp_path):
        path = self._write_jsonl(tmp_path, [
            {"type": "assistant"},  # no message field
            {"type": "assistant", "message": {}},  # no content field
        ])
        result = extract_mod.extract_last_assistant_text(path)
        assert result is None


# ---- format-question.py tests ----


class TestFormatQuestion:
    def test_empty_questions(self):
        result = format_mod.format_question({"tool_input": {"questions": []}})
        assert result == ""

    def test_no_questions_key(self):
        result = format_mod.format_question({})
        assert result == ""

    def test_basic_question(self):
        data = {
            "tool_input": {
                "questions": [{
                    "question": "Pick one",
                    "options": [
                        {"label": "Option A", "description": "First choice"},
                        {"label": "Option B", "description": "Second choice"},
                    ],
                }]
            }
        }
        result = format_mod.format_question(data)
        assert "Pick one" in result
        assert "Option A" in result
        assert "Option B" in result
        assert "First choice" in result

    def test_with_header(self):
        data = {
            "tool_input": {
                "questions": [{
                    "header": "Important",
                    "question": "Choose wisely",
                    "options": [],
                }]
            }
        }
        result = format_mod.format_question(data)
        assert "Important" in result
        assert "Choose wisely" in result

    def test_multi_select(self):
        data = {
            "tool_input": {
                "questions": [{
                    "question": "Select",
                    "multiSelect": True,
                    "options": [{"label": "A"}, {"label": "B"}],
                }]
            }
        }
        result = format_mod.format_question(data)
        assert "one or more" in result

    def test_input_key_fallback(self):
        """Falls back to 'input' key when 'tool_input' is missing."""
        data = {
            "input": {
                "questions": [{
                    "question": "Fallback?",
                    "options": [],
                }]
            }
        }
        result = format_mod.format_question(data)
        assert "Fallback?" in result

    def test_many_options(self):
        """More than 10 options uses numeric fallback."""
        options = [{"label": f"Opt{i}"} for i in range(12)]
        data = {
            "tool_input": {
                "questions": [{
                    "question": "Many opts",
                    "options": options,
                }]
            }
        }
        result = format_mod.format_question(data)
        assert "Opt11" in result
        assert "**11.**" in result  # fallback for index 10
