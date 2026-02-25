"""Dashboard configuration from environment variables and .notify-env file.

Follows the same config loading pattern as agent-bridge.py and slack-bridge.py:
reads from env vars first, then falls back to .notify-env file values.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)


@dataclass
class Config:
    """Dashboard configuration."""

    # Server
    host: str = "0.0.0.0"
    port: int = 8080

    # Database
    db_path: str = "/app/data/aily.db"

    # SSH hosts (comma-separated in env)
    ssh_hosts: list[str] = field(default_factory=lambda: ["localhost"])

    # Discord
    discord_bot_token: str = ""
    discord_channel_id: str = ""

    # Slack
    slack_bot_token: str = ""
    slack_app_token: str = ""
    slack_channel_id: str = ""

    # Auth
    dashboard_token: str = ""

    # Public URLs (used in templates and install scripts)
    dashboard_url: str = ""
    github_repo: str = "jiunbae/aily"

    # Worker intervals (seconds)
    poll_interval: int = 30
    ingest_interval: int = 15

    # Feature flags
    enable_session_poller: bool = True
    enable_message_ingester: bool = False  # Phase 2 feature, disabled by default
    enable_platform_sync: bool = True

    # JSONL ingestion settings
    enable_jsonl_ingester: bool = False  # disabled by default
    jsonl_scan_interval: int = 60        # seconds between JSONL scans
    jsonl_max_lines: int = 500           # max lines to tail per file
    jsonl_max_content_length: int = 5000 # truncate content longer than this

    # Usage monitoring
    anthropic_api_key: str = ""
    openai_api_key: str = ""
    enable_usage_poller: bool = False
    usage_poll_interval: int = 60
    usage_poll_model_anthropic: str = "claude-haiku-4-5-20251001"
    usage_poll_model_openai: str = "gpt-4o-mini"
    enable_command_queue: bool = False
    usage_retention_hours: int = 168  # 7 days

    # Agent auto-launch on !new
    new_session_agent: str = ""       # "claude", "codex", "gemini", "opencode", or ""
    claude_remote_control: bool = False

    # .notify-env path
    env_file: str = ""

    @classmethod
    def from_env(cls) -> Config:
        """Load configuration from environment variables, falling back to .notify-env."""
        config = cls()

        # Server settings
        config.host = os.environ.get("DASHBOARD_HOST", config.host)
        config.port = int(os.environ.get("DASHBOARD_PORT", str(config.port)))

        # Database
        config.db_path = os.environ.get("DASHBOARD_DB_PATH", config.db_path)

        # SSH hosts
        hosts_str = os.environ.get("SSH_HOSTS", "")
        if hosts_str:
            config.ssh_hosts = [h.strip() for h in hosts_str.split(",") if h.strip()]

        # Platform tokens from env
        config.discord_bot_token = os.environ.get("DISCORD_BOT_TOKEN", "")
        config.discord_channel_id = os.environ.get("DISCORD_CHANNEL_ID", "")
        config.slack_bot_token = os.environ.get("SLACK_BOT_TOKEN", "")
        config.slack_app_token = os.environ.get("SLACK_APP_TOKEN", "")
        config.slack_channel_id = os.environ.get("SLACK_CHANNEL_ID", "")

        # Auth
        config.dashboard_token = os.environ.get("DASHBOARD_TOKEN", "")

        # Public URLs
        config.dashboard_url = os.environ.get("DASHBOARD_URL", "")
        config.github_repo = os.environ.get("GITHUB_REPO", config.github_repo)

        # Worker intervals
        config.poll_interval = int(
            os.environ.get("POLL_INTERVAL", str(config.poll_interval))
        )
        config.ingest_interval = int(
            os.environ.get("INGEST_INTERVAL", str(config.ingest_interval))
        )

        # Feature flags
        config.enable_session_poller = (
            os.environ.get("ENABLE_SESSION_POLLER", "true").lower() != "false"
        )
        config.enable_message_ingester = (
            os.environ.get("ENABLE_MESSAGE_INGESTER", "false").lower() == "true"
        )
        config.enable_platform_sync = (
            os.environ.get("ENABLE_PLATFORM_SYNC", "true").lower() != "false"
        )

        # JSONL ingestion
        config.enable_jsonl_ingester = (
            os.environ.get("ENABLE_JSONL_INGESTER", "false").lower() == "true"
        )
        config.jsonl_scan_interval = int(
            os.environ.get("JSONL_SCAN_INTERVAL", str(config.jsonl_scan_interval))
        )
        config.jsonl_max_lines = int(
            os.environ.get("JSONL_MAX_LINES", str(config.jsonl_max_lines))
        )

        # Usage monitoring
        config.anthropic_api_key = os.environ.get("ANTHROPIC_API_KEY", "")
        config.openai_api_key = os.environ.get("OPENAI_API_KEY", "")
        config.enable_usage_poller = (
            os.environ.get("ENABLE_USAGE_POLLER", "false").lower() == "true"
        )
        try:
            config.usage_poll_interval = int(
                os.environ.get("USAGE_POLL_INTERVAL", str(config.usage_poll_interval))
            )
        except ValueError:
            logger.warning(
                "Invalid USAGE_POLL_INTERVAL, using default: %d",
                config.usage_poll_interval,
            )
        config.usage_poll_model_anthropic = os.environ.get(
            "USAGE_POLL_MODEL_ANTHROPIC", config.usage_poll_model_anthropic
        )
        config.usage_poll_model_openai = os.environ.get(
            "USAGE_POLL_MODEL_OPENAI", config.usage_poll_model_openai
        )
        config.enable_command_queue = (
            os.environ.get("ENABLE_COMMAND_QUEUE", "false").lower() == "true"
        )
        try:
            config.usage_retention_hours = int(
                os.environ.get(
                    "USAGE_RETENTION_HOURS", str(config.usage_retention_hours)
                )
            )
        except ValueError:
            logger.warning(
                "Invalid USAGE_RETENTION_HOURS, using default: %d",
                config.usage_retention_hours,
            )

        # Agent auto-launch
        config.new_session_agent = (
            os.environ.get("NEW_SESSION_AGENT", "").lower().strip()
        )
        config.claude_remote_control = (
            os.environ.get("CLAUDE_REMOTE_CONTROL", "false").lower() == "true"
        )

        # Fallback: load .notify-env file (same format as bridges)
        env_file = os.environ.get("AGENT_BRIDGE_ENV", "")
        if env_file and Path(env_file).exists():
            config.env_file = env_file
            _load_notify_env(config, env_file)

        return config


def _load_notify_env(config: Config, path: str) -> None:
    """Load values from .notify-env file (same format bridges use).

    Only fills in values not already set by environment variables.
    This mirrors how agent-bridge.py and slack-bridge.py load config.
    """
    env: dict[str, str] = {}
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "=" in line:
                key, val = line.split("=", 1)
                env[key.strip()] = val.strip().strip('"').strip("'")

    # Only fill values not already set from env vars
    if not config.discord_bot_token:
        config.discord_bot_token = env.get("DISCORD_BOT_TOKEN", "")
    if not config.discord_channel_id:
        config.discord_channel_id = env.get("DISCORD_CHANNEL_ID", "")
    if not config.slack_bot_token:
        config.slack_bot_token = env.get("SLACK_BOT_TOKEN", "")
    if not config.slack_app_token:
        config.slack_app_token = env.get("SLACK_APP_TOKEN", "")
    if not config.slack_channel_id:
        config.slack_channel_id = env.get("SLACK_CHANNEL_ID", "")

    # Dashboard auth token (CLI stores as AILY_AUTH_TOKEN)
    if not config.dashboard_token:
        config.dashboard_token = env.get("AILY_AUTH_TOKEN", "")

    # SSH hosts from .notify-env if not already set from env var
    if config.ssh_hosts == ["localhost"]:
        hosts = env.get("SSH_HOSTS", "")
        if hosts:
            config.ssh_hosts = [h.strip() for h in hosts.split(",") if h.strip()]

    # API keys for usage monitoring
    if not config.anthropic_api_key:
        config.anthropic_api_key = env.get("ANTHROPIC_API_KEY", "")
    if not config.openai_api_key:
        config.openai_api_key = env.get("OPENAI_API_KEY", "")

    # Agent auto-launch from .notify-env if not already set
    if not config.new_session_agent:
        config.new_session_agent = env.get("NEW_SESSION_AGENT", "").lower().strip()
    if not config.claude_remote_control:
        config.claude_remote_control = (
            env.get("CLAUDE_REMOTE_CONTROL", "false").lower() == "true"
        )

    logger.info("Loaded config from %s", path)
