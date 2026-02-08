#!/usr/bin/env python3
"""Format AskUserQuestion tool input as a Discord message."""
import json
import os
import sys

EMOJI_NUMBERS = ["1\ufe0f\u20e3", "2\ufe0f\u20e3", "3\ufe0f\u20e3", "4\ufe0f\u20e3",
                 "5\ufe0f\u20e3", "6\ufe0f\u20e3", "7\ufe0f\u20e3", "8\ufe0f\u20e3",
                 "9\ufe0f\u20e3", "\U0001f51f"]


def format_question(data: dict) -> str:
    """Format PreToolUse AskUserQuestion JSON into a Discord message."""
    tool_input = data.get("tool_input", data.get("input", {}))
    questions = tool_input.get("questions", [])
    if not questions:
        return ""

    hostname = os.environ.get("HOSTNAME", "unknown")
    project = os.environ.get("PROJECT", "unknown")
    timestamp = os.environ.get("TIMESTAMP", "")

    parts = ["\u2753 **Waiting for Input**\n"]

    for q in questions:
        header = q.get("header", "")
        question = q.get("question", "")
        options = q.get("options", [])
        multi = q.get("multiSelect", False)

        if header:
            parts.append(f"\U0001f4cb **{header}**")
        parts.append(f"**{question}**\n")

        if multi:
            parts.append("*(Select one or more)*\n")

        for i, opt in enumerate(options):
            emoji = EMOJI_NUMBERS[i] if i < len(EMOJI_NUMBERS) else f"**{i+1}.**"
            label = opt.get("label", "")
            desc = opt.get("description", "")
            parts.append(f"{emoji} **{label}**")
            if desc:
                parts.append(f"   {desc}")
            parts.append("")

    footer = []
    if hostname != "unknown":
        footer.append(f"\U0001f5a5 `{hostname}`")
    if project != "unknown":
        footer.append(f"\U0001f4c1 `{project}`")
    if timestamp:
        footer.append(f"\u23f0 {timestamp}")
    if footer:
        parts.append(" \u00b7 ".join(footer))

    option_nums = ", ".join(str(i+1) for i in range(len(questions[0].get("options", []))))
    if option_nums:
        parts.append(f"\U0001f4ac Reply with option number ({option_nums}) or type a custom answer")

    return "\n".join(parts)


def main():
    try:
        data = json.load(sys.stdin)
    except (json.JSONDecodeError, EOFError):
        sys.exit(1)

    msg = format_question(data)
    if msg:
        print(msg)
    else:
        sys.exit(1)


if __name__ == "__main__":
    main()
