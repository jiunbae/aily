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


def detect_session_limit(
    pre_content: str,
    post_content: str,
    user_message: Optional[str] = None,
) -> Optional[str]:
    """Compare pane content before and after sending a message.

    Returns the matched error line if session limit detected, None otherwise.

    ``user_message`` is the text we just sent. The pane echoes it back as new
    output, so a message that itself mentions "rate limit", "429", etc. would
    otherwise be misdetected as an error — and, once queued, the retry loop
    would re-send it and re-trigger on its own echo indefinitely. Lines that
    contain the (non-trivial) echoed message are therefore ignored.
    """
    pre_lines = set(pre_content.splitlines())
    new_lines = [line for line in post_content.splitlines() if line not in pre_lines]

    echo_fragments: list[str] = []
    if user_message:
        for frag in user_message.splitlines():
            frag = frag.strip()
            # Ignore very short fragments to avoid over-filtering genuine errors.
            if len(frag) >= 8:
                echo_fragments.append(frag)

    for line in new_lines:
        stripped = line.strip()
        if any(frag in stripped for frag in echo_fragments):
            continue
        for pattern in SESSION_LIMIT_PATTERNS:
            if pattern.search(line):
                return stripped
    return None
