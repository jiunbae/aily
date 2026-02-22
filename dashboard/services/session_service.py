"""tmux session management service.

Provides high-level session operations using the SSH helper module.
Mirrors the patterns from agent-bridge.py and slack-bridge.py but
exposes them as async methods with parallel host queries.
"""

from __future__ import annotations

import asyncio
import logging
import re

from dashboard import ssh

logger = logging.getLogger(__name__)

SESSION_NAME_RE = re.compile(r"^[a-zA-Z0-9_-]+$")

# Infrastructure sessions to skip when discovering sessions
INFRA_SESSIONS = frozenset({"agent-bridge", "slack-bridge"})


class SessionService:
    """Manages tmux sessions across SSH hosts.

    All SSH operations use asyncio.create_subprocess_exec under the hood
    (via the ssh module) for non-blocking execution.
    """

    def __init__(self, ssh_hosts: list[str]) -> None:
        self.ssh_hosts = ssh_hosts
        self.default_host = ssh_hosts[0] if ssh_hosts else ""

    @staticmethod
    def is_valid_session_name(name: str) -> bool:
        """Check if session name is safe for use in shell commands.

        Same regex as both bridges: ^[a-zA-Z0-9_-]+$, max 64 chars.
        """
        return bool(SESSION_NAME_RE.match(name)) and len(name) <= 64

    async def find_host(self, session_name: str) -> str | None:
        """Find which SSH host has a tmux session with this name.

        Queries all hosts in parallel using asyncio.gather for faster
        lookup when multiple hosts are configured.

        Args:
            session_name: tmux session name to search for.

        Returns:
            The hostname, or None if not found on any host.
        """
        if len(self.ssh_hosts) <= 1:
            # Single host — no need for parallel overhead
            for host in self.ssh_hosts:
                if await ssh.has_session(host, session_name):
                    return host
            return None

        # Parallel query all hosts
        async def check(host: str) -> str | None:
            return host if await ssh.has_session(host, session_name) else None

        results = await asyncio.gather(
            *(check(h) for h in self.ssh_hosts),
            return_exceptions=True,
        )
        for r in results:
            if isinstance(r, str):
                return r
        return None

    async def list_all_sessions(self) -> dict[str, list[str]]:
        """Return {host: [session_name, ...]} for all configured hosts.

        Queries all hosts in parallel using asyncio.gather.
        Filters out infrastructure sessions (agent-bridge, slack-bridge).

        Returns:
            Dict mapping hostname to list of session names.
        """
        results: dict[str, list[str]] = {}

        async def query_host(host: str) -> None:
            try:
                sessions = await ssh.list_tmux_sessions(host)
                # Filter out infrastructure sessions
                results[host] = [
                    name for name in sessions if name not in INFRA_SESSIONS
                ]
            except Exception:
                logger.exception("Failed to query tmux on %s", host)
                results[host] = []

        await asyncio.gather(*(query_host(h) for h in self.ssh_hosts))
        return results

    async def create_session(
        self, name: str, host: str, working_dir: str | None = None
    ) -> bool:
        """Create a new detached tmux session on the specified host.

        Args:
            name: Session name (must pass is_valid_session_name).
            host: SSH host to create on.
            working_dir: Initial working directory (default: home dir).

        Returns:
            True if creation succeeded.
        """
        return await ssh.create_tmux_session(host, name, working_dir)

    async def kill_session(self, name: str) -> tuple[bool, str | None]:
        """Kill a tmux session, searching all hosts for it first.

        Args:
            name: Session name to kill.

        Returns:
            Tuple of (success, hostname_or_none).
        """
        host = await self.find_host(name)
        if not host:
            logger.warning("Cannot kill '%s': not found on any host", name)
            return False, None
        success = await ssh.kill_tmux_session(host, name)
        return success, host

    async def send_to_session(
        self, host: str, session: str, message: str
    ) -> bool:
        """Send a message to a tmux session.

        Args:
            host: SSH host where the session lives.
            session: tmux session name.
            message: Text to send.

        Returns:
            True if send succeeded.
        """
        return await ssh.send_to_tmux(host, session, message)

    async def get_session_cwd(
        self, host: str, session_name: str
    ) -> str | None:
        """Get the current working directory of a session.

        Args:
            host: SSH host where the session lives.
            session_name: tmux session name.

        Returns:
            The working directory path, or None.
        """
        return await ssh.get_session_cwd(host, session_name)
