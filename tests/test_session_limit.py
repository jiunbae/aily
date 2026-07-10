"""Tests for session limit detection patterns."""

from __future__ import annotations

import pytest

from session_limit import detect_session_limit, SESSION_LIMIT_PATTERNS


class TestDetectSessionLimit:
    """Test detect_session_limit with various rate limit error messages."""

    def test_no_limit_detected(self):
        pre = "line1\nline2\nline3"
        post = "line1\nline2\nline3\nNormal output here"
        assert detect_session_limit(pre, post) is None

    def test_rate_limit_detected(self):
        pre = "line1\nline2"
        post = "line1\nline2\nError: Rate limit exceeded. Please try again later."
        result = detect_session_limit(pre, post)
        assert result is not None
        assert "Rate limit" in result

    def test_429_detected(self):
        pre = "some content"
        post = "some content\nHTTP 429 Too Many Requests"
        result = detect_session_limit(pre, post)
        assert result is not None

    def test_too_many_requests(self):
        pre = ""
        post = "Too many requests. Please wait before trying again."
        result = detect_session_limit(pre, post)
        assert result is not None

    def test_overloaded(self):
        pre = "prompt>"
        post = "prompt>\nThe API is currently overloaded. Please try again later."
        result = detect_session_limit(pre, post)
        assert result is not None

    def test_overcapacity(self):
        pre = ""
        post = "Service overcapacity, request rejected"
        result = detect_session_limit(pre, post)
        assert result is not None

    def test_session_limit(self):
        pre = "line1"
        post = "line1\nYou have hit the session limit for this model."
        result = detect_session_limit(pre, post)
        assert result is not None

    def test_try_again_in_minutes(self):
        pre = ""
        post = "try again in 30 minutes"
        result = detect_session_limit(pre, post)
        assert result is not None

    def test_try_again_in_seconds(self):
        pre = ""
        post = "Please try again in 60 seconds"
        result = detect_session_limit(pre, post)
        assert result is not None

    def test_usage_limit(self):
        pre = "content"
        post = "content\nUsage limit reached for today."
        result = detect_session_limit(pre, post)
        assert result is not None

    def test_quota_exceeded(self):
        pre = ""
        post = "Error: quota exceeded for this billing period"
        result = detect_session_limit(pre, post)
        assert result is not None

    def test_temporarily_unavailable(self):
        pre = "old"
        post = "old\nService temporarily unavailable"
        result = detect_session_limit(pre, post)
        assert result is not None

    def test_request_limit_reached(self):
        pre = ""
        post = "Request limit reached. Upgrade your plan."
        result = detect_session_limit(pre, post)
        assert result is not None

    def test_rate_limit_hyphenated(self):
        pre = ""
        post = "rate-limit error occurred"
        result = detect_session_limit(pre, post)
        assert result is not None

    def test_please_try_again_later(self):
        pre = ""
        post = "Something went wrong. Please try again later"
        result = detect_session_limit(pre, post)
        assert result is not None

    def test_empty_pre_content(self):
        pre = ""
        post = ""
        assert detect_session_limit(pre, post) is None

    def test_identical_content(self):
        pre = "line1\nline2"
        post = "line1\nline2"
        assert detect_session_limit(pre, post) is None

    def test_case_insensitive(self):
        pre = ""
        post = "RATE LIMIT EXCEEDED"
        result = detect_session_limit(pre, post)
        assert result is not None

    def test_only_new_lines_checked(self):
        """Lines that existed in pre_content should not trigger detection."""
        pre = "Rate limit exceeded\nold line"
        post = "Rate limit exceeded\nold line\nNew normal output"
        assert detect_session_limit(pre, post) is None

    def test_strips_whitespace_from_result(self):
        pre = ""
        post = "  Rate limit exceeded  "
        result = detect_session_limit(pre, post)
        assert result == "Rate limit exceeded"

    def test_echoed_user_message_ignored(self):
        """A user message that mentions a limit keyword must NOT self-trigger.

        The pane echoes the message we just sent; without excluding it, the
        detector would enqueue a retry that re-sends the same text and
        re-triggers on its own echo forever.
        """
        user_message = "why do I keep getting a rate limit error here?"
        pre = "prompt>"
        post = f"prompt>\n{user_message}"
        assert detect_session_limit(pre, post, user_message) is None

    def test_real_limit_still_detected_with_user_message(self):
        """The agent's actual error is still caught even when a user_message is given."""
        user_message = "run the deploy script"
        pre = "prompt>"
        post = f"prompt>\n{user_message}\nError: Rate limit exceeded. Try again later."
        result = detect_session_limit(pre, post, user_message)
        assert result is not None
        assert "Rate limit" in result

    def test_multiline_echoed_message_ignored(self):
        user_message = "line one is long enough\nusage limit reached is my question"
        pre = "prompt>"
        post = "prompt>\nline one is long enough\nusage limit reached is my question"
        assert detect_session_limit(pre, post, user_message) is None


class TestPatternCount:
    """Ensure all expected patterns are registered."""

    def test_pattern_count(self):
        assert len(SESSION_LIMIT_PATTERNS) == 12
