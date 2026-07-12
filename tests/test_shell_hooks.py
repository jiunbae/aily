"""Smoke tests for the shell scripts (hooks, CLI, installers).

These are deliberately lightweight — they don't need a live Discord/Slack — but
they catch the whole class of bugs that shipped before: syntax errors and
bash-4-only constructs that break on the macOS system bash (3.2).
"""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent

SHELL_SCRIPTS = sorted(
    [p for p in (REPO_ROOT / "hooks").glob("*.sh")]
    + [REPO_ROOT / "aily", REPO_ROOT / "install.sh", REPO_ROOT / "docker-entrypoint.sh"]
)

# bash 4+ only. macOS ships bash 3.2, where these raise "bad substitution" and,
# under `set -e`, abort the script — silently killing notifications / commands.
BASH4_ONLY = re.compile(r"\$\{[A-Za-z_][A-Za-z0-9_]*(,,|\^\^|,|\^)\}")


@pytest.mark.skipif(shutil.which("bash") is None, reason="bash not available")
@pytest.mark.parametrize("script", SHELL_SCRIPTS, ids=lambda p: p.name)
def test_shell_syntax(script: Path):
    """Every shell script must pass `bash -n` (parse without executing)."""
    result = subprocess.run(
        ["bash", "-n", str(script)], capture_output=True, text=True
    )
    assert result.returncode == 0, f"{script.name}: {result.stderr}"


@pytest.mark.parametrize("script", SHELL_SCRIPTS, ids=lambda p: p.name)
def test_no_bash4_only_expansions(script: Path):
    """No bash-4-only case-conversion expansions (break macOS bash 3.2)."""
    # Strip comments so a comment *mentioning* the forbidden syntax (e.g. a
    # "use tr, not ${var,,}" note) doesn't trip the check.
    code_lines = []
    for line in script.read_text().splitlines():
        stripped = line.lstrip()
        if stripped.startswith("#"):
            continue
        code_lines.append(re.sub(r"\s#.*$", "", line))
    text = "\n".join(code_lines)
    hits = [m.group(0) for m in BASH4_ONLY.finditer(text)]
    assert not hits, (
        f"{script.name} uses bash 4+ case-conversion expansion(s) {hits}; "
        f"use `tr` for macOS bash 3.2 compatibility"
    )


def test_gemini_hook_parses_stdin_json():
    """notify-gemini.sh must extract transcript_path/cwd from stdin JSON.

    Regression: a `<<'PY'` heredoc plus a `<<<` here-string collided so python
    executed the JSON as source and the parse silently produced empty values.
    Here we exercise the exact parsing snippet the hook uses.
    """
    if shutil.which("python3") is None:
        pytest.skip("python3 not available")
    snippet = (
        'import json,sys\n'
        'try: data=json.load(sys.stdin)\n'
        'except Exception: data={}\n'
        'print(data.get("transcript_path") or "")\n'
        'print(data.get("cwd") or "")\n'
    )
    payload = '{"transcript_path":"/tmp/foo.jsonl","cwd":"/tmp/work"}'
    result = subprocess.run(
        ["python3", "-c", snippet], input=payload,
        capture_output=True, text=True,
    )
    lines = result.stdout.splitlines()
    assert lines[0] == "/tmp/foo.jsonl"
    assert lines[1] == "/tmp/work"


@pytest.mark.parametrize("library", ["discord-lib.sh", "slack-lib.sh"])
def test_platform_curl_wrappers_have_deadlines(library: str):
    text = (REPO_ROOT / "hooks" / library).read_text()

    assert "--connect-timeout" in text
    assert "--max-time" in text


def test_cli_dashboard_requests_have_deadlines():
    text = (REPO_ROOT / "aily").read_text()
    api_call = text.split("api_call() {", 1)[1].split("\n}\n", 1)[0]

    assert "--connect-timeout" in api_call
    assert "--max-time" in api_call
