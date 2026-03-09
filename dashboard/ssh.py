"""Async SSH helper for terminal multiplexer session management.

Uses asyncio.create_subprocess_exec for non-blocking SSH operations.
Supports tmux (full) and zellij (partial) via the multiplexer abstraction.

SSH ControlMaster: Reuses persistent connections to avoid TCP+key
handshake overhead on repeated calls to the same host (~750ms -> ~50ms).
"""

from __future__ import annotations

import asyncio
import logging
import os
import shlex

from multiplexer import get_backend, Multiplexer

logger = logging.getLogger(__name__)

SEND_KEYS_DELAY = 0.3

# SSH ControlMaster: reuse connections to the same host.
# Socket path uses %r@%h:%p to uniquely identify each host connection.
_CONTROL_DIR = os.path.expanduser("~/.ssh/aily-ctl")
_SSH_CONTROL_OPTS = [
    "-o", "ControlMaster=auto",
    "-o", f"ControlPath={_CONTROL_DIR}/%r@%h:%p",
    "-o", "ControlPersist=300",
    "-o", "ConnectTimeout=5",
    "-o", "StrictHostKeyChecking=accept-new",
    "-o", "BatchMode=yes",
]

# Module-level multiplexer backend (initialized lazily)
_mux: Multiplexer | None = None


def _get_mux() -> Multiplexer:
    """Get the multiplexer backend, initializing on first call."""
    global _mux
    if _mux is None:
        _mux = get_backend()
        logger.info("Using multiplexer backend: %s", _mux.name)
    return _mux


def set_backend(mux_type: str) -> None:
    """Override the multiplexer backend (call before any SSH operations)."""
    global _mux
    _mux = get_backend(mux_type)
    logger.info("Multiplexer backend set to: %s", _mux.name)


def _ensure_control_dir() -> None:
    """Create the SSH control socket directory if it doesn't exist."""
    os.makedirs(_CONTROL_DIR, mode=0o700, exist_ok=True)


async def run_ssh(host: str, cmd: str, timeout: int = 15) -> tuple[int, str]:
    """Run a command over SSH asynchronously.

    Uses asyncio.create_subprocess_exec (not subprocess.run) to avoid
    blocking the event loop. SSH ControlMaster reuses persistent
    connections for ~15x speedup on repeated calls.

    Args:
        host: SSH host to connect to.
        cmd: Command to execute on the remote host.
        timeout: Maximum time to wait in seconds.

    Returns:
        Tuple of (return_code, stdout_output).
    """
    _ensure_control_dir()
    try:
        proc = await asyncio.create_subprocess_exec(
            "ssh", *_SSH_CONTROL_OPTS, host, cmd,
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


async def list_sessions(host: str) -> list[str]:
    """List multiplexer session names on a host.

    Args:
        host: SSH host to query.

    Returns:
        List of session name strings.
    """
    mux = _get_mux()
    cmd = mux.list_sessions_cmd() + " 2>/dev/null || true"
    rc, out = await run_ssh(host, cmd)
    sessions: list[str] = []
    if rc == 0 and out:
        for name in out.strip().split("\n"):
            name = name.strip()
            if name:
                sessions.append(name)
    return sessions


# Keep old name as alias for backward compatibility
list_tmux_sessions = list_sessions


async def send_to_session(host: str, session: str, message: str) -> bool:
    """Send keystrokes to a multiplexer session.

    Uses the two-step pattern:
      1. Send text (send-keys / write-chars)
      2. sleep 0.3s
      3. Send Enter

    This is critical for Claude Code -- a single send-keys with trailing
    Enter does not work reliably.

    Args:
        host: SSH host where the session lives.
        session: Session name.
        message: Text to type into the session.

    Returns:
        True if both steps succeeded.
    """
    mux = _get_mux()
    safe_session = shlex.quote(session)
    safe_message = shlex.quote(message)

    # Step 1: Type the text
    rc, _ = await run_ssh(host, mux.send_keys_cmd(safe_session, safe_message))
    if rc != 0:
        return False

    # Step 2: Press Enter (separate command -- critical for Claude Code)
    await asyncio.sleep(SEND_KEYS_DELAY)
    rc, _ = await run_ssh(host, mux.send_enter_cmd(safe_session))
    return rc == 0


# Keep old name as alias for backward compatibility
send_to_tmux = send_to_session


async def get_session_cwd(host: str, session_name: str) -> str | None:
    """Get the current working directory of a session's active pane.

    Args:
        host: SSH host where the session lives.
        session_name: Session name.

    Returns:
        The working directory path, or None if not available/supported.
    """
    mux = _get_mux()
    if not mux.supports_cwd:
        return None

    safe = shlex.quote(session_name)
    rc, out = await run_ssh(
        host,
        mux.get_cwd_cmd(safe) + " 2>/dev/null",
    )
    return out if rc == 0 and out else None


async def has_session(host: str, session_name: str) -> bool:
    """Check if a multiplexer session exists on a host.

    Args:
        host: SSH host to check.
        session_name: Session name.

    Returns:
        True if the session exists.
    """
    mux = _get_mux()
    safe = shlex.quote(session_name)
    rc, out = await run_ssh(
        host, mux.has_session_cmd(safe) + " 2>/dev/null && echo found"
    )
    return rc == 0 and "found" in out


async def create_session(
    host: str, name: str, working_dir: str | None = None
) -> bool:
    """Create a new detached multiplexer session.

    Args:
        host: SSH host to create the session on.
        name: Session name.
        working_dir: Initial working directory (default: home dir).

    Returns:
        True if creation succeeded.
    """
    mux = _get_mux()
    safe = shlex.quote(name)
    safe_dir = shlex.quote(working_dir) if working_dir else None
    cmd = mux.new_session_cmd(safe, safe_dir)
    rc, _ = await run_ssh(host, cmd)
    if rc == 0:
        logger.info(
            "Created %s session '%s' on '%s' (cwd=%s)",
            mux.name, name, host, working_dir or "~",
        )
    else:
        logger.error(
            "Failed to create %s session '%s' on '%s'",
            mux.name, name, host,
        )
    return rc == 0


# Keep old name as alias for backward compatibility
create_tmux_session = create_session


async def kill_session(host: str, name: str) -> bool:
    """Kill a multiplexer session.

    Args:
        host: SSH host where the session lives.
        name: Session name.

    Returns:
        True if the kill succeeded.
    """
    mux = _get_mux()
    safe = shlex.quote(name)
    rc, _ = await run_ssh(host, mux.kill_session_cmd(safe))
    if rc == 0:
        logger.info("Killed %s session '%s' on '%s'", mux.name, name, host)
    else:
        logger.error(
            "Failed to kill %s session '%s' on '%s'", mux.name, name, host
        )
    return rc == 0


# Keep old name as alias for backward compatibility
kill_tmux_session = kill_session
