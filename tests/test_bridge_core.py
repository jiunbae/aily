"""Tests for bridge_core.py static/class methods and module-level helpers."""

from __future__ import annotations

import sys
import os

import pytest

# Ensure the project root is on sys.path so bridge_core can be imported
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from bridge_core import BridgeCore, AGENT_PREFIX


# ---------------------------------------------------------------------------
# Helpers — lightweight BridgeCore with no real state
# ---------------------------------------------------------------------------

class _FakeMux:
    pass


class _FakePlatform:
    platform_name = "test"
    max_message_len = 2000


def _make_core(thread_name_format: str = "{session}@{host}") -> BridgeCore:
    """Create a BridgeCore with minimal fakes for instance-method tests."""
    from unittest.mock import MagicMock

    state = MagicMock()
    state.thread_name_format = thread_name_format
    state.default_host = "myhost"
    return BridgeCore(state=state, platform=_FakePlatform())


# ===================================================================
# _redact_secrets
# ===================================================================

class TestRedactSecrets:
    def test_redacts_api_key_equals(self):
        text = "api_key=sk-abc123secret"
        result = BridgeCore._redact_secrets(text)
        assert "sk-abc123secret" not in result
        assert "[REDACTED]" in result

    def test_redacts_token_colon(self):
        text = "token: ghp_SuperSecretToken123"
        result = BridgeCore._redact_secrets(text)
        assert "ghp_SuperSecretToken123" not in result
        assert "[REDACTED]" in result

    def test_redacts_password_quoted(self):
        text = 'password="my_s3cr3t_pw"'
        result = BridgeCore._redact_secrets(text)
        assert "my_s3cr3t_pw" not in result
        assert "[REDACTED]" in result

    def test_redacts_bearer(self):
        text = "bearer= eyJhbGciOiJIUzI1NiJ9.test"
        result = BridgeCore._redact_secrets(text)
        assert "eyJhbGciOiJIUzI1NiJ9" not in result

    def test_redacts_pem_key(self):
        text = (
            "some text\n"
            "-----BEGIN RSA PRIVATE KEY-----\n"
            "MIIBogIBAAJBAK...\n"
            "-----END RSA PRIVATE KEY-----\n"
            "more text"
        )
        result = BridgeCore._redact_secrets(text)
        assert "MIIBogIBAAJBAK" not in result
        assert "[REDACTED PEM KEY]" in result
        # Surrounding text survives
        assert "some text" in result
        assert "more text" in result

    def test_no_match_passes_through(self):
        text = "Hello world, nothing secret here"
        assert BridgeCore._redact_secrets(text) == text

    def test_empty_string(self):
        assert BridgeCore._redact_secrets("") == ""


# ===================================================================
# _validate_path
# ===================================================================

class TestValidatePath:
    def test_normal_absolute_path(self):
        assert BridgeCore._validate_path("/home/user/project") is True

    def test_relative_path(self):
        assert BridgeCore._validate_path("src/main.py") is True

    def test_tilde_path(self):
        assert BridgeCore._validate_path("~/workspace") is True

    def test_path_with_at_and_colon(self):
        assert BridgeCore._validate_path("user@host:/path") is True

    def test_directory_traversal_rejected(self):
        assert BridgeCore._validate_path("/home/user/../etc/shadow") is False

    def test_shell_semicolon_rejected(self):
        assert BridgeCore._validate_path("/tmp; rm -rf /") is False

    def test_shell_pipe_rejected(self):
        assert BridgeCore._validate_path("/tmp | cat /etc/passwd") is False

    def test_shell_backtick_rejected(self):
        assert BridgeCore._validate_path("/tmp/`whoami`") is False

    def test_shell_dollar_paren_rejected(self):
        assert BridgeCore._validate_path("/tmp/$(id)") is False

    def test_empty_string_rejected(self):
        assert BridgeCore._validate_path("") is False


# ===================================================================
# is_valid_session_name
# ===================================================================

class TestIsValidSessionName:
    def test_valid_alphanumeric(self):
        assert BridgeCore.is_valid_session_name("my-session_01") is True

    def test_valid_single_char(self):
        assert BridgeCore.is_valid_session_name("a") is True

    def test_empty_string(self):
        assert BridgeCore.is_valid_session_name("") is False

    def test_too_long(self):
        assert BridgeCore.is_valid_session_name("a" * 65) is False

    def test_exactly_64(self):
        assert BridgeCore.is_valid_session_name("a" * 64) is True

    def test_special_chars_rejected(self):
        assert BridgeCore.is_valid_session_name("my session") is False
        assert BridgeCore.is_valid_session_name("my;session") is False
        assert BridgeCore.is_valid_session_name("my/session") is False
        assert BridgeCore.is_valid_session_name("my.session") is False

    def test_dash_and_underscore_allowed(self):
        assert BridgeCore.is_valid_session_name("foo-bar_baz") is True


# ===================================================================
# parse_thread_name (instance method, needs format template)
# ===================================================================

class TestParseThreadName:
    def test_standard_format(self):
        core = _make_core("{session}@{host}")
        assert core.parse_thread_name("my-session@myhost") == "my-session"

    def test_standard_format_different_host(self):
        core = _make_core("{session}@{host}")
        assert core.parse_thread_name("work_01@remote-server") == "work_01"

    def test_legacy_agent_prefix(self):
        core = _make_core("{session}@{host}")
        assert core.parse_thread_name("[agent] legacy-sess") == "legacy-sess"

    def test_malformed_returns_none(self):
        core = _make_core("{session}@{host}")
        assert core.parse_thread_name("completely random text") is None

    def test_empty_string_returns_none(self):
        core = _make_core("{session}@{host}")
        assert core.parse_thread_name("") is None

    def test_custom_format(self):
        core = _make_core("[{host}] {session}")
        assert core.parse_thread_name("[server1] my-sess") == "my-sess"


# ===================================================================
# _is_prompt_line
# ===================================================================

class TestIsPromptLine:
    def test_dollar_prompt(self):
        assert BridgeCore._is_prompt_line("user@host:~$ ") is True

    def test_percent_prompt(self):
        assert BridgeCore._is_prompt_line("% ") is True

    def test_chevron_prompt(self):
        assert BridgeCore._is_prompt_line("> ") is True

    def test_unicode_chevron(self):
        assert BridgeCore._is_prompt_line("  \u276f") is True

    def test_box_drawing_decorations(self):
        line = "\u2500" * 10 + " prompt area"
        assert BridgeCore._is_prompt_line(line) is True

    def test_non_prompt_normal_text(self):
        assert BridgeCore._is_prompt_line("Hello world") is False

    def test_non_prompt_code_output(self):
        assert BridgeCore._is_prompt_line("  return 42") is False

    def test_empty_string_not_prompt(self):
        assert BridgeCore._is_prompt_line("") is False

    def test_whitespace_only_not_prompt(self):
        assert BridgeCore._is_prompt_line("   ") is False

    def test_ansi_codes_stripped(self):
        # Prompt with ANSI color codes wrapping it
        line = "\x1b[32muser@host\x1b[0m:\x1b[34m~\x1b[0m$ "
        assert BridgeCore._is_prompt_line(line) is True

    def test_ansi_codes_non_prompt(self):
        line = "\x1b[31mERROR: something failed\x1b[0m"
        assert BridgeCore._is_prompt_line(line) is False
