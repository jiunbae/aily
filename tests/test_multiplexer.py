"""Tests for multiplexer.py — TmuxBackend, ZellijBackend, detection, factory."""

from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from multiplexer import (
    TmuxBackend,
    ZellijBackend,
    detect_multiplexer,
    get_backend,
    MultiplexerType,
)


# ===================================================================
# TmuxBackend command generation
# ===================================================================

class TestTmuxBackend:
    @pytest.fixture
    def tmux(self):
        return TmuxBackend()

    def test_name(self, tmux):
        assert tmux.name == "tmux"

    def test_has_session_cmd(self, tmux):
        cmd = tmux.has_session_cmd("mysess")
        assert cmd == "tmux has-session -t mysess"

    def test_list_sessions_cmd(self, tmux):
        cmd = tmux.list_sessions_cmd()
        assert "tmux list-sessions" in cmd
        assert "session_name" in cmd

    def test_new_session_cmd_no_dir(self, tmux):
        cmd = tmux.new_session_cmd("test-sess")
        assert "tmux new-session -d -s test-sess" in cmd
        assert "-c" not in cmd

    def test_new_session_cmd_with_dir(self, tmux):
        cmd = tmux.new_session_cmd("test-sess", "/home/user")
        assert "-s test-sess" in cmd
        assert "-c /home/user" in cmd

    def test_send_keys_cmd(self, tmux):
        cmd = tmux.send_keys_cmd("sess", "'hello'")
        assert cmd == "tmux send-keys -t sess 'hello'"

    def test_send_enter_cmd(self, tmux):
        cmd = tmux.send_enter_cmd("sess")
        assert cmd == "tmux send-keys -t sess Enter"

    def test_send_raw_key_cmd(self, tmux):
        cmd = tmux.send_raw_key_cmd("sess", "C-c")
        assert cmd == "tmux send-keys -t sess C-c"

    def test_capture_pane_cmd(self, tmux):
        cmd = tmux.capture_pane_cmd("sess")
        assert cmd == "tmux capture-pane -t sess -p"

    def test_kill_session_cmd(self, tmux):
        cmd = tmux.kill_session_cmd("sess")
        assert cmd == "tmux kill-session -t sess"

    def test_get_pane_command_cmd(self, tmux):
        cmd = tmux.get_pane_command_cmd("sess")
        assert "pane_current_command" in cmd

    def test_get_cwd_cmd(self, tmux):
        cmd = tmux.get_cwd_cmd("sess")
        assert "pane_current_path" in cmd

    def test_set_environment_cmd(self, tmux):
        cmd = tmux.set_environment_cmd("sess", "MY_VAR", "'value'")
        assert "set-environment" in cmd
        assert "MY_VAR" in cmd

    def test_supports_flags(self, tmux):
        assert tmux.supports_pane_command is True
        assert tmux.supports_cwd is True
        assert tmux.supports_environment is True
        assert tmux.supports_session_hooks is True
        assert tmux.supports_detached_capture is True


# ===================================================================
# ZellijBackend command generation
# ===================================================================

class TestZellijBackend:
    @pytest.fixture
    def zellij(self):
        return ZellijBackend()

    def test_name(self, zellij):
        assert zellij.name == "zellij"

    def test_has_session_cmd(self, zellij):
        cmd = zellij.has_session_cmd("mysess")
        assert "zellij list-sessions" in cmd
        assert "grep" in cmd

    def test_list_sessions_cmd(self, zellij):
        cmd = zellij.list_sessions_cmd()
        assert "zellij list-sessions" in cmd

    def test_new_session_cmd_no_dir(self, zellij):
        cmd = zellij.new_session_cmd("test-sess")
        assert "zellij -s test-sess" in cmd
        assert "nohup" in cmd

    def test_new_session_cmd_with_dir(self, zellij):
        cmd = zellij.new_session_cmd("test-sess", "/home/user")
        assert "cd /home/user" in cmd
        assert "zellij -s test-sess" in cmd

    def test_send_keys_cmd(self, zellij):
        cmd = zellij.send_keys_cmd("sess", "'hello'")
        assert "zellij -s sess action write-chars 'hello'" == cmd

    def test_send_enter_cmd(self, zellij):
        cmd = zellij.send_enter_cmd("sess")
        assert "zellij -s sess action write 13" == cmd

    def test_send_raw_key_cmd_ctrl_c(self, zellij):
        cmd = zellij.send_raw_key_cmd("sess", "C-c")
        assert "action write 3" in cmd

    def test_send_raw_key_cmd_ctrl_d(self, zellij):
        cmd = zellij.send_raw_key_cmd("sess", "C-d")
        assert "action write 4" in cmd

    def test_send_raw_key_cmd_escape(self, zellij):
        cmd = zellij.send_raw_key_cmd("sess", "Escape")
        assert "action write 27" in cmd

    def test_send_raw_key_cmd_enter(self, zellij):
        cmd = zellij.send_raw_key_cmd("sess", "Enter")
        assert "action write 13" in cmd

    def test_send_raw_key_cmd_q(self, zellij):
        cmd = zellij.send_raw_key_cmd("sess", "q")
        assert "action write 113" in cmd

    def test_send_raw_key_cmd_unsupported(self, zellij):
        with pytest.raises(ValueError, match="Unsupported key"):
            zellij.send_raw_key_cmd("sess", "F5")

    def test_capture_pane_cmd(self, zellij):
        cmd = zellij.capture_pane_cmd("sess")
        assert "dump-screen" in cmd
        assert "mktemp" in cmd

    def test_kill_session_cmd(self, zellij):
        cmd = zellij.kill_session_cmd("sess")
        assert cmd == "zellij delete-session sess --force"

    def test_get_pane_command_cmd_unsupported(self, zellij):
        cmd = zellij.get_pane_command_cmd("sess")
        assert cmd == "echo ''"

    def test_get_cwd_cmd_unsupported(self, zellij):
        cmd = zellij.get_cwd_cmd("sess")
        assert cmd == "echo ''"

    def test_set_environment_cmd_unsupported(self, zellij):
        cmd = zellij.set_environment_cmd("sess", "VAR", "val")
        assert cmd == "true"

    def test_supports_flags(self, zellij):
        assert zellij.supports_pane_command is False
        assert zellij.supports_cwd is False
        assert zellij.supports_environment is False
        assert zellij.supports_session_hooks is False
        assert zellij.supports_detached_capture is True


# ===================================================================
# detect_multiplexer
# ===================================================================

class TestDetectMultiplexer:
    def test_explicit_override(self, monkeypatch):
        monkeypatch.setenv("AILY_MULTIPLEXER", "zellij")
        monkeypatch.delenv("ZELLIJ", raising=False)
        monkeypatch.delenv("TMUX", raising=False)
        assert detect_multiplexer() == "zellij"

    def test_explicit_override_case_insensitive(self, monkeypatch):
        monkeypatch.setenv("AILY_MULTIPLEXER", "ZELLIJ")
        assert detect_multiplexer() == "zellij"

    def test_zellij_env(self, monkeypatch):
        monkeypatch.delenv("AILY_MULTIPLEXER", raising=False)
        monkeypatch.setenv("ZELLIJ", "0")
        monkeypatch.delenv("TMUX", raising=False)
        assert detect_multiplexer() == "zellij"

    def test_tmux_env(self, monkeypatch):
        monkeypatch.delenv("AILY_MULTIPLEXER", raising=False)
        monkeypatch.delenv("ZELLIJ", raising=False)
        monkeypatch.setenv("TMUX", "/tmp/tmux-1000/default,12345,0")
        assert detect_multiplexer() == "tmux"

    def test_default_is_tmux(self, monkeypatch):
        monkeypatch.delenv("AILY_MULTIPLEXER", raising=False)
        monkeypatch.delenv("ZELLIJ", raising=False)
        monkeypatch.delenv("TMUX", raising=False)
        assert detect_multiplexer() == "tmux"

    def test_explicit_overrides_zellij_env(self, monkeypatch):
        monkeypatch.setenv("AILY_MULTIPLEXER", "tmux")
        monkeypatch.setenv("ZELLIJ", "0")
        assert detect_multiplexer() == "tmux"


# ===================================================================
# get_backend
# ===================================================================

class TestGetBackend:
    def test_get_tmux(self):
        backend = get_backend("tmux")
        assert isinstance(backend, TmuxBackend)

    def test_get_zellij(self):
        backend = get_backend("zellij")
        assert isinstance(backend, ZellijBackend)

    def test_case_insensitive(self):
        backend = get_backend("TMUX")
        assert isinstance(backend, TmuxBackend)

    def test_unsupported_raises(self):
        with pytest.raises(ValueError, match="Unsupported multiplexer"):
            get_backend("screen")

    def test_auto_detect(self, monkeypatch):
        monkeypatch.delenv("AILY_MULTIPLEXER", raising=False)
        monkeypatch.delenv("ZELLIJ", raising=False)
        monkeypatch.delenv("TMUX", raising=False)
        backend = get_backend()  # should default to tmux
        assert isinstance(backend, TmuxBackend)
