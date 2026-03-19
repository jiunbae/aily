"""Terminal multiplexer abstraction layer for aily.

Supports tmux (full support) and zellij (partial support).
Selection priority:
  1. AILY_MULTIPLEXER env var (explicit override)
  2. ZELLIJ env var set -> zellij
  3. TMUX env var set -> tmux
  4. Default: tmux
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from enum import Enum
from typing import Optional
import os


class MultiplexerType(Enum):
    TMUX = "tmux"
    ZELLIJ = "zellij"


@dataclass
class SessionInfo:
    """Common session information across multiplexers."""
    name: str
    attached: bool = False


class Multiplexer(ABC):
    """Abstract base class for terminal multiplexer backends."""

    @abstractmethod
    def send_keys_cmd(self, session: str, text: str) -> str:
        """Return shell command to send text to a session."""
        ...

    @abstractmethod
    def send_enter_cmd(self, session: str) -> str:
        """Return shell command to send Enter key."""
        ...

    @abstractmethod
    def send_raw_key_cmd(self, session: str, key: str) -> str:
        """Return shell command to send a raw key (e.g., C-c, Escape)."""
        ...

    @abstractmethod
    def capture_pane_cmd(self, session: str) -> str:
        """Return shell command to capture pane content. Output should go to stdout."""
        ...

    @abstractmethod
    def has_session_cmd(self, session: str) -> str:
        """Return shell command to check if session exists (exit code 0 = exists)."""
        ...

    @abstractmethod
    def list_sessions_cmd(self) -> str:
        """Return shell command to list session names, one per line."""
        ...

    @abstractmethod
    def new_session_cmd(self, name: str, working_dir: Optional[str] = None) -> str:
        """Return shell command to create a detached session."""
        ...

    @abstractmethod
    def kill_session_cmd(self, name: str) -> str:
        """Return shell command to kill/delete a session."""
        ...

    @abstractmethod
    def get_pane_command_cmd(self, session: str) -> str:
        """Return shell command to get the current running command in the pane.
        Returns empty string command if not supported."""
        ...

    @abstractmethod
    def get_cwd_cmd(self, session: str) -> str:
        """Return shell command to get the pane's current working directory.
        Returns empty string command if not supported."""
        ...

    @abstractmethod
    def set_environment_cmd(self, session: str, var: str, value: str) -> str:
        """Return shell command to set an environment variable in the session.
        Returns a no-op command if not supported."""
        ...

    @property
    @abstractmethod
    def name(self) -> str:
        """Return the multiplexer name."""
        ...

    @property
    def supports_pane_command(self) -> bool:
        """Whether this multiplexer supports querying the running command."""
        return True

    @property
    def supports_cwd(self) -> bool:
        """Whether this multiplexer supports querying the current directory."""
        return True

    @property
    def supports_environment(self) -> bool:
        """Whether this multiplexer supports setting session environment variables."""
        return True

    @property
    def supports_session_hooks(self) -> bool:
        """Whether this multiplexer supports session lifecycle hooks."""
        return True

    @property
    def supports_detached_capture(self) -> bool:
        """Whether this multiplexer supports capturing pane content from detached sessions."""
        return True


class TmuxBackend(Multiplexer):
    """tmux terminal multiplexer backend (full support)."""

    @property
    def name(self) -> str:
        return "tmux"

    def send_keys_cmd(self, session: str, text: str) -> str:
        # session and text should be pre-quoted by the caller (shlex.quote)
        return f"tmux send-keys -t {session} {text}"

    def send_enter_cmd(self, session: str) -> str:
        return f"tmux send-keys -t {session} Enter"

    def send_raw_key_cmd(self, session: str, key: str) -> str:
        return f"tmux send-keys -t {session} {key}"

    def capture_pane_cmd(self, session: str) -> str:
        return f"tmux capture-pane -t {session} -p"

    def has_session_cmd(self, session: str) -> str:
        return f"tmux has-session -t {session}"

    def list_sessions_cmd(self) -> str:
        return "tmux list-sessions -F '#{session_name}'"

    def new_session_cmd(self, name: str, working_dir: Optional[str] = None) -> str:
        # name and working_dir should be pre-quoted by the caller
        cmd = f"tmux new-session -d -s {name}"
        if working_dir:
            cmd += f" -c {working_dir}"
        return cmd

    def kill_session_cmd(self, name: str) -> str:
        return f"tmux kill-session -t {name}"

    def get_pane_command_cmd(self, session: str) -> str:
        return f"tmux display-message -t {session} -p '#{{pane_current_command}}'"

    def get_cwd_cmd(self, session: str) -> str:
        return f"tmux display-message -t {session} -p '#{{pane_current_path}}'"

    def set_environment_cmd(self, session: str, var: str, value: str) -> str:
        # var is not quoted (env var names are safe), value should be pre-quoted
        return f"tmux set-environment -t {session} {var} {value}"


class ZellijBackend(Multiplexer):
    """Zellij terminal multiplexer backend (partial support).

    Known limitations:
    - Cannot query pane's running command
    - Cannot query pane's current working directory
    - Cannot set session environment variables
    - No built-in session lifecycle hooks (requires WASM plugin)
    - dump-screen writes to file, not stdout (we work around this)
    - dump-screen doesn't work on detached sessions
    """

    @property
    def name(self) -> str:
        return "zellij"

    @property
    def supports_pane_command(self) -> bool:
        return False

    @property
    def supports_cwd(self) -> bool:
        return False

    @property
    def supports_environment(self) -> bool:
        return False

    @property
    def supports_session_hooks(self) -> bool:
        return False

    @property
    def supports_detached_capture(self) -> bool:
        return False

    def send_keys_cmd(self, session: str, text: str) -> str:
        # session and text should be pre-quoted by the caller (shlex.quote)
        return f"zellij -s {session} action write-chars {text}"

    def send_enter_cmd(self, session: str) -> str:
        return f"zellij -s {session} action write 13"

    def send_raw_key_cmd(self, session: str, key: str) -> str:
        # Map tmux-style key names to byte values
        key_map = {
            'C-c': '3', 'C-d': '4', 'C-z': '26',
            'Escape': '27', 'Enter': '13',
            'q': '113',
        }
        byte_val = key_map.get(key, key)
        if not byte_val.isdigit():
            raise ValueError(f"Unsupported key for zellij: {key}")
        return f"zellij -s {session} action write {byte_val}"

    def capture_pane_cmd(self, session: str) -> str:
        # Use mktemp to avoid predictable tmp paths (security: symlink attacks)
        return (
            f'tmp=$(mktemp /tmp/zellij-capture-XXXXXX) && '
            f'zellij -s {session} action dump-screen "$tmp" && '
            f'cat "$tmp"; rm -f "$tmp"'
        )

    _STRIP_ANSI = r"s/\x1b\[[0-9;]*m//g"

    def has_session_cmd(self, session: str) -> str:
        # Assign pre-quoted session to a shell var, then use grep -Fx for
        # exact fixed-string line match. Avoids regex injection and prefix collisions.
        # zellij list-sessions outputs ANSI color codes and metadata, so we
        # strip colors first, then extract only the name column.
        return (
            f'name={session}; '
            f'zellij list-sessions 2>/dev/null | sed -e "{self._STRIP_ANSI}" -e "s/ .*//" | grep -qFx -- "$name"'
        )

    def list_sessions_cmd(self) -> str:
        # zellij list-sessions outputs ANSI color codes + extra info; strip both
        return f"zellij list-sessions 2>/dev/null | sed -e '{self._STRIP_ANSI}' -e 's/ .*//'"

    def new_session_cmd(self, name: str, working_dir: Optional[str] = None) -> str:
        # Zellij doesn't have a clean detached session creation like tmux.
        # Background the process, then verify it started.
        # name and working_dir should be pre-quoted by the caller
        if working_dir:
            launch = f"cd {working_dir} && zellij -s {name} &>/dev/null &"
        else:
            launch = f"zellij -s {name} &>/dev/null &"
        # Verify session actually started (background launch always exits 0)
        verify = f'sleep 1 && _n={name} && zellij list-sessions 2>/dev/null | sed -e \'{self._STRIP_ANSI}\' | grep -qE "^$_n($| )"'
        return f"{launch} {verify}"

    def kill_session_cmd(self, name: str) -> str:
        return f"zellij delete-session {name} --force"

    def get_pane_command_cmd(self, session: str) -> str:
        # Not supported - return empty string
        return "echo ''"

    def get_cwd_cmd(self, session: str) -> str:
        # Not supported - return empty string
        return "echo ''"

    def set_environment_cmd(self, session: str, var: str, value: str) -> str:
        # Not supported - no-op
        return "true"


def detect_multiplexer() -> str:
    """Auto-detect the multiplexer type from environment.

    Priority:
      1. AILY_MULTIPLEXER env var (explicit override)
      2. ZELLIJ env var set -> zellij
      3. TMUX env var set -> tmux
      4. Default: tmux
    """
    explicit = os.environ.get("AILY_MULTIPLEXER", "").lower().strip()
    if explicit:
        return explicit

    if os.environ.get("ZELLIJ"):
        return "zellij"
    if os.environ.get("TMUX"):
        return "tmux"

    return "tmux"


def get_backend(mux_type: Optional[str] = None) -> Multiplexer:
    """Factory function to get a multiplexer backend.

    Args:
        mux_type: "tmux" or "zellij". If None, auto-detects.

    Returns:
        Multiplexer backend instance.
    """
    if mux_type is None:
        mux_type = detect_multiplexer()

    backends = {
        "tmux": TmuxBackend,
        "zellij": ZellijBackend,
    }
    mux_type = mux_type.lower()
    if mux_type not in backends:
        raise ValueError(
            f"Unsupported multiplexer: {mux_type}. "
            f"Supported: {list(backends.keys())}"
        )
    return backends[mux_type]()
