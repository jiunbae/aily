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


class TestPatternCount:
    """Ensure all expected patterns are registered."""

    def test_pattern_count(self):
        assert len(SESSION_LIMIT_PATTERNS) == 12
