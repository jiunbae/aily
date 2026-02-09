#!/usr/bin/env python3
"""Codex CLI notification hook entry point.

Codex passes a JSON object as sys.argv[1] with `last-assistant-message`.
Extracts the response and calls post.sh (multi-platform dispatcher).

Registered in ~/.codex/config.toml:
  notify = ["python3", "/path/to/notify-codex.py"]
"""
import json
import os
import subprocess
import sys


def main():
    if len(sys.argv) < 2:
        return

    try:
        notification = json.loads(sys.argv[1])
    except (json.JSONDecodeError, TypeError):
        return

    if not isinstance(notification, dict):
        return

    if notification.get("type") != "agent-turn-complete":
        return

    # Only notify for runs inside tmux sessions (thread is based on tmux session name).
    if not (os.environ.get("TMUX") or os.environ.get("TMUX_PANE")):
        return

    last_message = notification.get("last-assistant-message") or ""
    if not isinstance(last_message, str):
        last_message = str(last_message)
    if len(last_message.strip()) < 20:
        return

    hook_dir = os.path.dirname(os.path.abspath(__file__))
    if not os.path.isfile(os.path.join(hook_dir, ".notify-env")):
        return
    post_script = os.path.join(hook_dir, "post.sh")
    if not os.path.isfile(post_script):
        return

    cwd = notification.get("cwd")
    if not isinstance(cwd, str) or not cwd or not os.path.isdir(cwd):
        cwd = os.getcwd()

    # Background the post so Codex isn't blocked by network calls.
    try:
        subprocess.Popen(
            ["bash", post_script, "codex", last_message],
            cwd=cwd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
    except Exception:
        return


if __name__ == "__main__":
    main()
