"""Settings from environment variables or a local .env file (never committed)."""
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

# Anchored to the project root so it works from any working directory.
# A .env in the current directory, if any, takes precedence.
_ROOT_ENV = Path(__file__).resolve().parents[2] / ".env"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(_ROOT_ENV, ".env"), env_file_encoding="utf-8-sig", extra="ignore"
    )

    database_url: str
    telegram_bot_token: str
    telegram_chat_id: str
    secret_key: str  # signs login sessions; keep it private and stable

    # Dashboard is internal-only (FR-29). Set true if served over HTTPS.
    cookie_secure: bool = False
    session_hours: int = 12

    probe_interval_s: int = 30
    probe_count: int = 4
    probe_timeout_s: float = 2.0
    confirm: int = 3
    reminder_minutes: int = 30  # 0 disables reminders (FR-13)
    retention_days: int = 30  # raw checks older than this are deleted; 0 keeps everything
    bot_enabled: bool = True
    weekly_report_enabled: bool = True  # Monday 08:00 uptime summary to Telegram
    # Dead-man's-switch ping URL, e.g. https://hc-ping.com/<uuid>. Unset disables it.
    heartbeat_url: str | None = None
    heartbeat_interval_s: int = 60
    # Default canary is TCP: many networks block ICMP to public addresses.
    canary_host: str = "1.1.1.1"
    canary_port: int = 443
    canary_type: str = "tcp"
    canary_enabled: bool = True
    # Pre-filled in the 'Add branch' form.
    default_latency_warn_ms: float = 200
    default_loss_warn_pct: float = 20
