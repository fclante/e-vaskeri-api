import os
from dataclasses import dataclass, field


def _float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, default))
    except ValueError:
        return default


def _bool(name: str, default: bool) -> bool:
    value = os.environ.get(name)
    return default if value is None else value.strip().lower() in ("1", "true", "yes", "on")


@dataclass(frozen=True)
class Config:
    base_url: str = field(default_factory=lambda: os.environ.get("E_VASKERI_BASE_URL", "https://back-nortec.datpro2.dk").rstrip("/"))
    username: str | None = field(default_factory=lambda: os.environ.get("E_VASKERI_USERNAME") or None)
    password: str | None = field(default_factory=lambda: os.environ.get("E_VASKERI_PASSWORD") or None)
    api_key: str | None = field(default_factory=lambda: os.environ.get("API_KEY") or None)
    log_level: str = field(default_factory=lambda: os.environ.get("LOG_LEVEL", "INFO").upper())
    machines_ttl: float = field(default_factory=lambda: _float("MACHINES_TTL_SECONDS", 20))
    account_ttl: float = field(default_factory=lambda: _float("ACCOUNT_TTL_SECONDS", 600))
    connect_timeout: float = field(default_factory=lambda: _float("UPSTREAM_CONNECT_TIMEOUT", 5))
    read_timeout: float = field(default_factory=lambda: _float("UPSTREAM_READ_TIMEOUT", 10))
    ready_window: float = field(default_factory=lambda: _float("READY_WINDOW_SECONDS", 300))
    calendar_ttl: float = field(default_factory=lambda: _float("CALENDAR_TTL_SECONDS", 120))
    # Refresh1 change detection. Account data is refetched only when the CRC changed, or after the max age.
    refresh_check: bool = field(default_factory=lambda: _bool("REFRESH_CHECK", True))
    account_max_age: float = field(default_factory=lambda: _float("ACCOUNT_MAX_AGE_SECONDS", 3600))
    # Live data: only observed by default. Skipping refetches needs the CRC to be proven reliable first.
    refresh_shadow_live: bool = field(default_factory=lambda: _bool("REFRESH_SHADOW_LIVE", True))
    refresh_skip_live: bool = field(default_factory=lambda: _bool("REFRESH_SKIP_LIVE", False))
    live_max_age: float = field(default_factory=lambda: _float("LIVE_MAX_AGE_SECONDS", 300))

    @property
    def credentials_configured(self) -> bool:
        return bool(self.username and self.password)
