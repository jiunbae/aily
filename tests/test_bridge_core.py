"""Tests for bridge_core.py static/class methods and module-level helpers."""

from __future__ import annotations

import asyncio
import sys
import os
import threading
from types import SimpleNamespace

import pytest

# Ensure the project root is on sys.path so bridge_core can be imported
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from bridge_core import BridgeCore, AGENT_PREFIX


# ---------------------------------------------------------------------------
# Helpers — lightweight BridgeCore with no real state
# ---------------------------------------------------------------------------

class _FakeMux:
    pass


class _FakePlatform:
    platform_name = "test"
    max_message_len = 2000

    def __init__(self):
        self.messages = []

    async def post_message(self, channel_id, text, **kwargs):
        self.messages.append((channel_id, text, kwargs))


def _make_core(thread_name_format: str = "{session}@{host}") -> BridgeCore:
    """Create a BridgeCore with minimal fakes for instance-method tests."""
    from unittest.mock import MagicMock

    state = MagicMock()
    state.thread_name_format = thread_name_format
    state.default_host = "myhost"
    state.authorized_users = set()
    state.allow_all_users = False
    state.ssh_failures = {}
    state.ssh_failure_lock = threading.Lock()
    return BridgeCore(state=state, platform=_FakePlatform())


# ===================================================================
# _redact_secrets
# ===================================================================

class TestRedactSecrets:
    def test_redacts_api_key_equals(self):
        text = "api_key=sk-abc123secret"
        result = BridgeCore._redact_secrets(text)
        assert "sk-abc123secret" not in result
        assert "[REDACTED]" in result

    def test_redacts_token_colon(self):
        text = "token: ghp_SuperSecretToken123"
        result = BridgeCore._redact_secrets(text)
        assert "ghp_SuperSecretToken123" not in result
        assert "[REDACTED]" in result

    def test_redacts_password_quoted(self):
        text = 'password="my_s3cr3t_pw"'
        result = BridgeCore._redact_secrets(text)
        assert "my_s3cr3t_pw" not in result
        assert "[REDACTED]" in result

    def test_redacts_bearer(self):
        text = "bearer= eyJhbGciOiJIUzI1NiJ9.test"
        result = BridgeCore._redact_secrets(text)
        assert "eyJhbGciOiJIUzI1NiJ9" not in result

    def test_redacts_pem_key(self):
        text = (
            "some text\n"
            "-----BEGIN RSA PRIVATE KEY-----\n"
            "MIIBogIBAAJBAK...\n"
            "-----END RSA PRIVATE KEY-----\n"
            "more text"
        )
        result = BridgeCore._redact_secrets(text)
        assert "MIIBogIBAAJBAK" not in result
        assert "[REDACTED PEM KEY]" in result
        # Surrounding text survives
        assert "some text" in result
        assert "more text" in result

    def test_no_match_passes_through(self):
        text = "Hello world, nothing secret here"
        assert BridgeCore._redact_secrets(text) == text

    def test_empty_string(self):
        assert BridgeCore._redact_secrets("") == ""

    @pytest.mark.parametrize("secret", [
        "sk-" + "a" * 40,
        "ghp_" + "A" * 36,
        "AKIA" + "A" * 16,
        "xoxb-" + "a" * 30,
    ])
    def test_redacts_standalone_token_formats(self, secret):
        assert secret not in BridgeCore._redact_secrets(f"output: {secret}")

    def test_redacts_password_in_url(self):
        result = BridgeCore._redact_secrets("postgres://user:password@db/app")
        assert "password" not in result


# ===================================================================
# _validate_path
# ===================================================================

class TestValidatePath:
    def test_normal_absolute_path(self):
        assert BridgeCore._validate_path("/home/user/project") is True

    def test_relative_path(self):
        assert BridgeCore._validate_path("src/main.py") is True

    def test_tilde_path(self):
        assert BridgeCore._validate_path("~/workspace") is True

    def test_path_with_at_and_colon(self):
        assert BridgeCore._validate_path("user@host:/path") is True

    def test_directory_traversal_rejected(self):
        assert BridgeCore._validate_path("/home/user/../etc/shadow") is False

    def test_shell_semicolon_rejected(self):
        assert BridgeCore._validate_path("/tmp; rm -rf /") is False

    def test_shell_pipe_rejected(self):
        assert BridgeCore._validate_path("/tmp | cat /etc/passwd") is False

    def test_shell_backtick_rejected(self):
        assert BridgeCore._validate_path("/tmp/`whoami`") is False

    def test_shell_dollar_paren_rejected(self):
        assert BridgeCore._validate_path("/tmp/$(id)") is False

    def test_empty_string_rejected(self):
        assert BridgeCore._validate_path("") is False


# ===================================================================
# SSH execution safety
# ===================================================================

class TestRunSsh:
    def _core(self):
        state = SimpleNamespace(
            thread_name_format="{session}@{host}",
            default_host="host-a",
            authorized_users=set(),
            allow_all_users=False,
            ssh_failures={},
            ssh_failure_lock=threading.Lock(),
        )
        return BridgeCore(state=state, platform=_FakePlatform())

    def test_remote_uses_controlmaster_options(self, monkeypatch):
        calls = []

        class Result:
            returncode = 0
            stdout = "ok\n"

        def fake_run(args, **kwargs):
            calls.append((args, kwargs))
            return Result()

        monkeypatch.setattr("bridge_core._ensure_control_dir", lambda: None)
        monkeypatch.setattr("subprocess.run", fake_run)

        rc, out = self._core().run_ssh("host-a", "echo ok")

        assert (rc, out) == (0, "ok")
        args = calls[0][0]
        assert args[0] == "ssh"
        assert "ControlMaster=auto" in args
        assert "StrictHostKeyChecking=yes" in args
        assert args[-2:] == ["host-a", "echo ok"]

    def test_rejects_unsafe_remote_host_before_subprocess(self, monkeypatch):
        def fail_run(*args, **kwargs):
            raise AssertionError("subprocess.run should not be called")

        monkeypatch.setattr("subprocess.run", fail_run)

        rc, out = self._core().run_ssh("-oProxyCommand=sh", "id")

        assert (rc, out) == (1, "")

    def test_localhost_uses_local_shell(self, monkeypatch):
        calls = []

        class Result:
            returncode = 0
            stdout = "local\n"

        def fake_run(args, **kwargs):
            calls.append((args, kwargs))
            return Result()

        monkeypatch.setattr("subprocess.run", fake_run)

        rc, out = self._core().run_ssh("localhost", "printf local")

        assert (rc, out) == (0, "local")
        assert calls[0][0] == ["bash", "-c", "printf local"]

    def test_circuit_breaks_after_repeated_ssh_connection_failures(self, monkeypatch):
        calls = []

        class Result:
            returncode = 255
            stdout = ""

        def fake_run(args, **kwargs):
            calls.append(args)
            return Result()

        monkeypatch.setattr("bridge_core._ensure_control_dir", lambda: None)
        monkeypatch.setattr("subprocess.run", fake_run)
        core = self._core()

        for _ in range(3):
            assert core.run_ssh("host-a", "true") == (255, "")

        assert core.run_ssh("host-a", "true") == (1, "")
        assert len(calls) == 3


# ===================================================================
# Runtime configuration
# ===================================================================

class TestLoadEnv:
    def test_process_environment_overrides_config_file(self, tmp_path):
        config = tmp_path / "env"
        config.write_text("DISCORD_BOT_TOKEN=file-token\nSSH_HOSTS=file-host\n")

        env = BridgeCore.load_env(
            str(config),
            {"DISCORD_BOT_TOKEN": "process-token", "BRIDGE_MODE": "discord"},
        )

        assert env["DISCORD_BOT_TOKEN"] == "process-token"
        assert env["SSH_HOSTS"] == "file-host"
        assert env["BRIDGE_MODE"] == "discord"

    def test_missing_default_config_still_accepts_process_environment(self, tmp_path):
        env = BridgeCore.load_env(
            str(tmp_path / "missing"),
            {"SLACK_BOT_TOKEN": "xoxb-test"},
        )

        assert env == {"SLACK_BOT_TOKEN": "xoxb-test"}


# ===================================================================
# Background task lifecycle
# ===================================================================

class TestBackgroundTasks:
    @pytest.mark.asyncio
    async def test_pending_tasks_are_bounded_and_reaped(self, monkeypatch):
        state = SimpleNamespace(
            background_tasks=set(),
            background_sem=asyncio.Semaphore(1),
        )
        core = BridgeCore(state=state, platform=_FakePlatform())
        gate = asyncio.Event()

        async def blocked():
            await gate.wait()

        monkeypatch.setattr("bridge_core._MAX_PENDING_BACKGROUND_TASKS", 2)

        first = core.track_task(blocked())
        second = core.track_task(blocked())
        dropped = core.track_task(blocked())

        assert first is not None
        assert second is not None
        assert dropped is None
        assert len(state.background_tasks) == 2

        await core.shutdown()

        assert not state.background_tasks
        assert first.cancelled()
        assert second.cancelled()


class TestAsyncOutputCapture:
    @pytest.mark.asyncio
    async def test_polling_extracts_output_without_blocking_worker_during_waits(
        self, monkeypatch
    ):
        core = _make_core()
        snapshots = iter(["old\nnew", "old\nnew"])
        monkeypatch.setattr(core, "get_pane_command", lambda *_: "/bin/zsh")
        monkeypatch.setattr(
            core, "capture_pane_content", lambda *_: next(snapshots)
        )

        output = await core.capture_shell_output_async(
            "localhost",
            "work",
            "old",
            poll_interval=0,
            stable_count=1,
            max_wait=1,
            initial_delay=0,
        )

        assert output == "new"


class TestAsyncHostLookup:
    @pytest.mark.asyncio
    async def test_fans_out_hosts_without_nested_thread_pool(self, monkeypatch):
        core = _make_core()
        core.state.ssh_hosts = ["host-a", "host-b", "host-c"]
        checked = []

        def check(host, session_name, safe_name):
            checked.append((host, session_name, safe_name))
            return host if host == "host-b" else None

        monkeypatch.setattr(core, "_check_host_for_session", check)

        assert await core.find_session_host_async("work") == "host-b"
        assert any(host == "host-b" for host, _, _ in checked)


# ===================================================================
# is_valid_session_name
# ===================================================================

class TestIsValidSessionName:
    def test_valid_alphanumeric(self):
        assert BridgeCore.is_valid_session_name("my-session_01") is True

    def test_valid_single_char(self):
        assert BridgeCore.is_valid_session_name("a") is True

    def test_empty_string(self):
        assert BridgeCore.is_valid_session_name("") is False

    def test_too_long(self):
        assert BridgeCore.is_valid_session_name("a" * 65) is False

    def test_exactly_64(self):
        assert BridgeCore.is_valid_session_name("a" * 64) is True

    def test_special_chars_rejected(self):
        assert BridgeCore.is_valid_session_name("my session") is False
        assert BridgeCore.is_valid_session_name("my;session") is False
        assert BridgeCore.is_valid_session_name("my/session") is False
        assert BridgeCore.is_valid_session_name("my.session") is False

    def test_dash_and_underscore_allowed(self):
        assert BridgeCore.is_valid_session_name("foo-bar_baz") is True


# ===================================================================
# Authorization helpers
# ===================================================================

class TestAuthorization:
    @pytest.mark.asyncio
    async def test_authorized_users_gate_state_changing_commands(self):
        core = _make_core()
        core.state.authorized_users = {"U123"}

        await core.handle_command("C1", "!new work", user_id="U999")

        assert core.platform.messages == [
            ("C1", "Using bridge commands requires authorization.", {})
        ]

    @pytest.mark.asyncio
    async def test_authorized_users_gate_message_forwarding(self, monkeypatch):
        core = _make_core()
        core.state.authorized_users = {"U123"}

        def fail_lookup(*args, **kwargs):
            raise AssertionError("session lookup should not run for unauthorized users")

        async def fail_lookup_async(*args, **kwargs):
            fail_lookup(*args, **kwargs)

        monkeypatch.setattr(core, "find_session_host_async", fail_lookup_async)

        await core.relay_message("C1", "work", "hello", "someone", user_id="U999")

        assert core.platform.messages == [
            ("C1", "Forwarding messages requires authorization.", {})
        ]

    @pytest.mark.asyncio
    async def test_empty_authorized_users_fail_closed(self):
        core = _make_core()
        core.state.authorized_users = set()

        await core.handle_command("C1", "!new work", user_id="anyone")

        assert core.platform.messages == [
            ("C1", "Using bridge commands requires authorization.", {})
        ]

    @pytest.mark.asyncio
    async def test_allow_all_users_requires_explicit_override(self, monkeypatch):
        core = _make_core()
        core.state.allow_all_users = True

        async def fake_cmd_new(reply_channel, raw_args, user_id="", **reply_kwargs):
            await core.platform.post_message(reply_channel, f"allowed:{raw_args}")

        monkeypatch.setattr(core, "cmd_new", fake_cmd_new)

        await core.handle_command("C1", "!new work", user_id="anyone")

        assert core.platform.messages == [("C1", "allowed:work", {})]

    def test_load_common_config_parses_authorized_users(self, monkeypatch):
        monkeypatch.delenv("AUTHORIZED_USERS", raising=False)
        monkeypatch.delenv("AILY_AUTHORIZED_USERS", raising=False)

        state = BridgeCore.load_common_config({
            "AUTHORIZED_USERS": "U1, U2,, ",
            "SSH_HOSTS": "localhost",
        })

        assert state.authorized_users == {"U1", "U2"}
        assert state.allow_all_users is False

    def test_detects_bare_shell_processes(self):
        assert BridgeCore._is_bare_shell_process("/bin/zsh") is True
        assert BridgeCore._is_bare_shell_process("bash") is True
        assert BridgeCore._is_bare_shell_process("claude") is False


# ===================================================================
# parse_thread_name (instance method, needs format template)
# ===================================================================

class TestParseThreadName:
    def test_standard_format(self):
        core = _make_core("{session}@{host}")
        assert core.parse_thread_name("my-session@myhost") == "my-session"

    def test_standard_format_different_host(self):
        core = _make_core("{session}@{host}")
        assert core.parse_thread_name("work_01@remote-server") == "work_01"

    def test_legacy_agent_prefix(self):
        core = _make_core("{session}@{host}")
        assert core.parse_thread_name("[agent] legacy-sess") == "legacy-sess"

    def test_malformed_returns_none(self):
        core = _make_core("{session}@{host}")
        assert core.parse_thread_name("completely random text") is None

    def test_empty_string_returns_none(self):
        core = _make_core("{session}@{host}")
        assert core.parse_thread_name("") is None

    def test_custom_format(self):
        core = _make_core("[{host}] {session}")
        assert core.parse_thread_name("[server1] my-sess") == "my-sess"


# ===================================================================
# parse_thread_name (module-level, used by the Discord/Slack bridges)
# ===================================================================

class TestParseThreadNameModule:
    def test_default_format(self):
        from bridge_core import parse_thread_name
        assert parse_thread_name("[agent] my-sess - myhost") == "my-sess"

    def test_custom_format_honored(self):
        """The bridges pass the configured format; a non-default format must parse.

        Regression: the bridges used to call this with the hardcoded default, so
        any custom THREAD_NAME_FORMAT silently broke message routing.
        """
        from bridge_core import parse_thread_name
        assert parse_thread_name("session=work@host1", "session={session}@{host}") == "work"

    def test_custom_format_mismatch_returns_none(self):
        from bridge_core import parse_thread_name
        # Matches neither the custom format nor the legacy [agent] prefix fallback.
        assert parse_thread_name("random chatter", "session={session}@{host}") is None


# ===================================================================
# _is_prompt_line
# ===================================================================

class TestIsPromptLine:
    def test_dollar_prompt(self):
        assert BridgeCore._is_prompt_line("user@host:~$ ") is True

    def test_percent_prompt(self):
        assert BridgeCore._is_prompt_line("% ") is True

    def test_chevron_prompt(self):
        assert BridgeCore._is_prompt_line("> ") is True

    def test_unicode_chevron(self):
        assert BridgeCore._is_prompt_line("  \u276f") is True

    def test_box_drawing_decorations(self):
        line = "\u2500" * 10 + " prompt area"
        assert BridgeCore._is_prompt_line(line) is True

    def test_non_prompt_normal_text(self):
        assert BridgeCore._is_prompt_line("Hello world") is False

    def test_non_prompt_code_output(self):
        assert BridgeCore._is_prompt_line("  return 42") is False

    def test_empty_string_not_prompt(self):
        assert BridgeCore._is_prompt_line("") is False

    def test_whitespace_only_not_prompt(self):
        assert BridgeCore._is_prompt_line("   ") is False

    def test_ansi_codes_stripped(self):
        # Prompt with ANSI color codes wrapping it
        line = "\x1b[32muser@host\x1b[0m:\x1b[34m~\x1b[0m$ "
        assert BridgeCore._is_prompt_line(line) is True

    def test_ansi_codes_non_prompt(self):
        line = "\x1b[31mERROR: something failed\x1b[0m"
        assert BridgeCore._is_prompt_line(line) is False
