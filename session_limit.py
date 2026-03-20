"""Session limit detection for Claude Code / Codex rate-limit errors.

Compares pane content before and after sending a message to detect
session/rate limit errors in new output lines.
"""

from __future__ import annotations

import re
from typing import Optional

SESSION_LIMIT_PATTERNS = [
    re.compile(p, re.IGNORECASE) for p in [
        r"rate limit|rate-limit",
        r"Too many requests",
        r"over(?:loaded|capacity)",
        r"Please try again (?:later|in)",
        r"session limit",
        r"\b429\b",
        r"Request limit reached",
        r"usage limit",
        r"Rate limit exceeded",
        r"quota exceeded",
        r"temporarily unavailable",
        r"try again in \d+ (?:minute|second|hour)",
    ]
]


def detect_session_limit(pre_content: str, post_content: str) -> Optional[str]:
    """Compare pane content before and after sending a message.

    Returns the matched error line if session limit detected, None otherwise.
    """
    pre_lines = set(pre_content.splitlines())
    new_lines = [line for line in post_content.splitlines() if line not in pre_lines]

    for line in new_lines:
        for pattern in SESSION_LIMIT_PATTERNS:
            if pattern.search(line):
                return line.strip()
    return None
