"""sources.yaml 로딩과 출처별 수집 실행."""
from __future__ import annotations

import logging
import time
from pathlib import Path

import yaml

from ..models import Group, RawArticle, SourceConfig
from .html_list import collect_html_list
from .rss import collect_rss

log = logging.getLogger(__name__)


def load_sources(path: Path, *, include_disabled: bool = False) -> list[SourceConfig]:
    data = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    out = [SourceConfig(**s) for s in data.get("sources", [])]
    ids = [s.id for s in out]
    dup = {i for i in ids if ids.count(i) > 1}
    if dup:
        raise ValueError(f"sources.yaml에 중복 id: {dup}")
    return out if include_disabled else [s for s in out if s.enabled]


def sources_by_group(sources: list[SourceConfig], *, wired: bool) -> list[SourceConfig]:
    return [s for s in sources if (s.group == Group.WIRED) == wired]


def collect_source(source: SourceConfig, settings, known_urls: set[str] | None = None) -> list[RawArticle]:
    kw = dict(user_agent=settings.http_user_agent, timeout=settings.http_timeout, retries=settings.http_retries)
    if source.type in ("rss", "arxiv_api"):
        items = collect_rss(source, **kw)
        if source.type == "arxiv_api":
            time.sleep(3)  # arXiv API 이용 정책: 연속 요청 간 3초
        return items
    if source.type == "html_list":
        return collect_html_list(source, known_urls=known_urls, **kw)
    raise ValueError(f"알 수 없는 수집 유형: {source.type}")
