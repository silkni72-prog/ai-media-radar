"""RSS/Atom 및 arXiv API(Atom) 수집기."""
from __future__ import annotations

import calendar
import logging
import re
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import feedparser

from ..models import RawArticle, SourceConfig
from .http import fetch
from .normalize import clean_text, normalize_url

log = logging.getLogger(__name__)


NAIVE_RX = re.compile(r"^\s*(\d{4}-\d{2}-\d{2})[ T](\d{2}:\d{2}(?::\d{2})?)\s*$")


def _entry_datetime(entry, naive_tz: str = "UTC") -> datetime | None:
    # 국내 CMS 일부는 '2026-11-09 09:35:01'처럼 시간대 없이 KST를 준다 → 출처 시간대로 해석
    keys = set(entry.keys())
    for key in ("published", "updated"):
        m = NAIVE_RX.match(entry[key] or "") if key in keys else None
        if m:
            local = datetime.fromisoformat(f"{m.group(1)}T{m.group(2)}").replace(tzinfo=ZoneInfo(naive_tz))
            return local.astimezone(timezone.utc).replace(tzinfo=None)
    for key in ("published_parsed", "updated_parsed", "created_parsed"):
        st = entry.get(key)
        if st:
            try:
                # feedparser는 UTC 기준 struct_time을 돌려준다
                return datetime.fromtimestamp(calendar.timegm(st), tz=timezone.utc).replace(tzinfo=None)
            except (OverflowError, ValueError):
                continue
    return None


def parse_feed(content: bytes, source: SourceConfig) -> list[RawArticle]:
    feed = feedparser.parse(content)
    if feed.bozo and not feed.entries:
        raise ValueError(f"피드 파싱 실패: {feed.get('bozo_exception')}")
    naive_tz = source.naive_tz or ("Asia/Seoul" if source.group.value.startswith("korea") else "UTC")
    future_limit = datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(hours=1)
    items: list[RawArticle] = []
    for e in feed.entries:
        link = e.get("link") or ""
        title = clean_text(e.get("title", ""), 300)
        if not link or not title:
            continue
        body = e.get("summary") or (e.content[0].get("value", "") if e.get("content") else "")
        published = _entry_datetime(e, naive_tz)
        if published and published > future_limit:  # 미래 시각 = 시간대 오류 가능성 → 불확실 처리
            log.warning("%s: 미래 발행 시각 %s → 발행 시각 미확인으로 처리", source.id, published)
            published = None
        items.append(RawArticle(
            source_id=source.id, source_name=source.name, group=source.group,
            url=normalize_url(link), title=title, summary=clean_text(body, 1500),
            author=clean_text(e.get("author", ""), 120), published_at=published,
            published_uncertain=published is None,
            default_category=source.default_category, prefilter=source.prefilter,
        ))
    # 최신순 정렬 후 max_items개만 (아카이브 전체를 주는 피드 대응)
    items.sort(key=lambda a: a.published_at or datetime.min, reverse=True)
    return items[: source.max_items]


def collect_rss(source: SourceConfig, *, user_agent: str, timeout: float, retries: int) -> list[RawArticle]:
    r = fetch(source.url, user_agent=user_agent, timeout=timeout, retries=retries)
    return parse_feed(r.content, source)
