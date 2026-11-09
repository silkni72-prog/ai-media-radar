"""환경변수 기반 설정. 비밀값은 코드에 두지 않는다."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent
KST = ZoneInfo("Asia/Seoul")

try:  # .env는 로컬 개발 편의용. GitHub Actions에서는 Secrets가 환경변수로 들어온다.
    from dotenv import load_dotenv

    load_dotenv(ROOT / ".env")
except ImportError:  # pragma: no cover
    pass


def _env(name: str, default: str = "") -> str:
    # .env에 'DATABASE_URL='처럼 빈 값으로 적힌 경우도 기본값을 쓴다
    return os.environ.get(name, "").strip() or default


def _int(name: str, default: int) -> int:
    try:
        return int(_env(name, str(default)))
    except ValueError:
        return default


def _bool(name: str, default: bool = False) -> bool:
    v = _env(name, "")
    if not v:
        return default
    return v.lower() in {"1", "true", "yes", "on"}


@dataclass
class Settings:
    database_url: str = field(default_factory=lambda: _env("DATABASE_URL", f"sqlite:///{(ROOT / 'data' / 'radar.db').as_posix()}"))
    sources_file: Path = field(default_factory=lambda: Path(_env("SOURCES_FILE", str(ROOT / "config" / "sources.yaml"))))
    output_dir: Path = field(default_factory=lambda: Path(_env("OUTPUT_DIR", str(ROOT / "output"))))

    # LLM
    llm_provider: str = field(default_factory=lambda: _env("LLM_PROVIDER", "none").lower())  # anthropic | openai | none
    llm_model: str = field(default_factory=lambda: _env("LLM_MODEL", ""))
    llm_briefing_model: str = field(default_factory=lambda: _env("LLM_BRIEFING_MODEL", ""))
    llm_max_articles_per_run: int = field(default_factory=lambda: _int("LLM_MAX_ARTICLES_PER_RUN", 120))

    # ntfy
    ntfy_base_url: str = field(default_factory=lambda: _env("NTFY_BASE_URL", "https://ntfy.sh").rstrip("/"))
    ntfy_daily_topic: str = field(default_factory=lambda: _env("NTFY_DAILY_TOPIC"))
    ntfy_wired_topic: str = field(default_factory=lambda: _env("NTFY_WIRED_TOPIC"))
    ntfy_alert_topic: str = field(default_factory=lambda: _env("NTFY_ALERT_TOPIC"))
    ntfy_token: str = field(default_factory=lambda: _env("NTFY_TOKEN"))
    ntfy_dry_run: bool = field(default_factory=lambda: _bool("NTFY_DRY_RUN", True))
    ntfy_max_retries: int = field(default_factory=lambda: _int("NTFY_MAX_RETRIES", 3))

    # 브리핑 공개 주소 (예: GitHub Pages). 비어 있으면 푸시 클릭 링크 없이 보낸다.
    briefing_public_base_url: str = field(default_factory=lambda: _env("BRIEFING_PUBLIC_BASE_URL").rstrip("/"))

    # 임계값
    wired_alert_threshold: int = field(default_factory=lambda: _int("WIRED_ALERT_THRESHOLD", 80))
    breaking_threshold: int = field(default_factory=lambda: _int("BREAKING_THRESHOLD", 90))
    breaking_all_sources: bool = field(default_factory=lambda: _bool("BREAKING_ALL_SOURCES", False))
    collect_lookback_hours: int = field(default_factory=lambda: _int("COLLECT_LOOKBACK_HOURS", 48))

    http_user_agent: str = field(default_factory=lambda: _env("HTTP_USER_AGENT", "AIMediaRadar/0.1 (+news-intelligence bot; RSS reader)"))
    http_timeout: float = 20.0
    http_retries: int = field(default_factory=lambda: _int("HTTP_RETRIES", 3))

    @property
    def llm_enabled(self) -> bool:
        return self.llm_provider in {"anthropic", "openai"}


def get_settings() -> Settings:
    return Settings()
