"""Async SSH helper for tmux session management.

Uses asyncio.create_subprocess_exec for non-blocking SSH operations.
Mirrors the SSH patterns from agent-bridge.py and slack-bridge.py:
  - shlex.quote() for all user-supplied values
  - Two-step send-keys pattern (type text, then press Enter)
  - 0.3s delay between send-keys steps
"""

from __future__ import annotations

import asyncio
import logging
import shlex

logger = logging.getLogger(__name__)

SEND_KEYS_DELAY = 0.3


async def run_ssh(host: str, cmd: str, timeout: int = 15) -> tuple[int, str]:
    """Run a command over SSH asynchronously.

    Uses asyncio.create_subprocess_exec (not subprocess.run) to avoid
    blocking the event loop.

    Args:
        host: SSH host to connect to.
        cmd: Command to execute on the remote host.
        timeout: Maximum time to wait in seconds.

    Returns:
        Tuple of (return_code, stdout_output).
    """
    try:
        proc = await asyncio.create_subprocess_exec(
            "ssh", host, cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(
            proc.communicate(), timeout=timeout
        )
        return proc.returncode or 0, stdout.decode().strip()
    except asyncio.TimeoutError:
        try:
            proc.kill()
            await proc.wait()
        except ProcessLookupError:
            pass
        logger.warning("SSH timeout: %s: %s", host, cmd[:80])
        return 1, ""
    except Exception as e:
        logger.error("SSH error: %s: %s: %s", host, cmd[:80], e)
        return 1, str(e)


async def list_tmux_sessions(host: str) -> list[str]:
    """List tmux session names on a host.

    Same tmux format string used by both bridges:
      tmux list-sessions -F '#{session_name}'

    Args:
        host: SSH host to query.

    Returns:
        List of session name strings.
    """
    rc, out = await run_ssh(
        host, "tmux list-sessions -F '#{session_name}' 2>/dev/null || true"
    )
    sessions: list[str] = []
    if rc == 0 and out:
        for name in out.strip().split("\n"):
            name = name.strip()
            if name:
                sessions.append(name)
    return sessions


async def send_to_tmux(host: str, session: str, message: str) -> bool:
    """Send keystrokes to a tmux session.

    Uses the same two-step pattern as agent-bridge.py and slack-bridge.py:
      1. tmux send-keys -t <session> <quoted-message>
      2. sleep 0.3s
      3. tmux send-keys -t <session> Enter

    This is critical for Claude Code — a single send-keys with trailing
    Enter does not work reliably.

    Args:
        host: SSH host where the tmux session lives.
        session: tmux session name.
        message: Text to type into the session.

    Returns:
        True if both steps succeeded.
    """
    safe_session = shlex.quote(session)
    safe_message = shlex.quote(message)

    # Step 1: Type the text
    rc, _ = await run_ssh(
        host, f"tmux send-keys -t {safe_session} {safe_message}"
    )
    if rc != 0:
        return False

    # Step 2: Press Enter (separate command — critical for Claude Code)
    await asyncio.sleep(SEND_KEYS_DELAY)
    rc, _ = await run_ssh(
        host, f"tmux send-keys -t {safe_session} Enter"
    )
    return rc == 0


async def get_session_cwd(host: str, session_name: str) -> str | None:
    """Get the current working directory of a tmux session's active pane.

    Uses tmux display-message to query the pane_current_path format variable.

    Args:
        host: SSH host where the tmux session lives.
        session_name: tmux session name.

    Returns:
        The working directory path, or None if not available.
    """
    safe = shlex.quote(session_name)
    rc, out = await run_ssh(
        host,
        f"tmux display-message -t {safe} -p '#{{pane_current_path}}' 2>/dev/null",
    )
    return out if rc == 0 and out else None


async def has_session(host: str, session_name: str) -> bool:
    """Check if a tmux session exists on a host.

    Same pattern as find_session_host in both bridges:
      tmux has-session -t <name> && echo found

    Args:
        host: SSH host to check.
        session_name: tmux session name.

    Returns:
        True if the session exists.
    """
    safe = shlex.quote(session_name)
    rc, out = await run_ssh(
        host, f"tmux has-session -t {safe} 2>/dev/null && echo found"
    )
    return rc == 0 and "found" in out


async def create_tmux_session(host: str, name: str) -> bool:
    """Create a new detached tmux session.

    Args:
        host: SSH host to create the session on.
        name: Session name.

    Returns:
        True if creation succeeded.
    """
    safe = shlex.quote(name)
    rc, _ = await run_ssh(host, f"tmux new-session -d -s {safe}")
    if rc == 0:
        logger.info("Created tmux session '%s' on '%s'", name, host)
    else:
        logger.error("Failed to create tmux session '%s' on '%s'", name, host)
    return rc == 0


async def kill_tmux_session(host: str, name: str) -> bool:
    """Kill a tmux session.

    Args:
        host: SSH host where the session lives.
        name: Session name.

    Returns:
        True if the kill succeeded.
    """
    safe = shlex.quote(name)
    rc, _ = await run_ssh(host, f"tmux kill-session -t {safe}")
    if rc == 0:
        logger.info("Killed tmux session '%s' on '%s'", name, host)
    else:
        logger.error("Failed to kill tmux session '%s' on '%s'", name, host)
    return rc == 0
