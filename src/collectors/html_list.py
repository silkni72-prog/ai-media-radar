"""공식 RSS가 없는 출처용 목록 페이지 수집기.

robots.txt가 허용할 때만 동작한다. 목록 페이지에서 기사 링크를 찾고, 처음 보는 기사만
상세 페이지의 공개 메타데이터(og:title, article:published_time)와 본문 첫 문단을 읽는다.
"""
from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from urllib.parse import urljoin, urlsplit
from urllib.robotparser import RobotFileParser

from bs4 import BeautifulSoup

from ..models import RawArticle, SourceConfig
from .http import fetch
from .normalize import clean_text, normalize_url

log = logging.getLogger(__name__)


class RobotsDisallowed(Exception):
    pass


def robots_allows(url: str, user_agent: str, *, timeout: float = 15.0) -> bool:
    parts = urlsplit(url)
    rp = RobotFileParser()
    try:
        r = fetch(f"{parts.scheme}://{parts.netloc}/robots.txt", user_agent=user_agent, timeout=timeout, retries=2)
        rp.parse(r.text.splitlines())
    except Exception as e:  # robots.txt를 못 읽으면 보수적으로 수집하지 않는다
        log.warning("robots.txt 확인 실패(%s) → 수집 보류: %s", url, e)
        return False
    return rp.can_fetch(user_agent, url)


def _parse_iso(s: str | None) -> datetime | None:
    if not s:
        return None
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo:
        dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt


def extract_links(html: str, base_url: str, pattern: str, exclude: list[str]) -> list[tuple[str, str]]:
    soup = BeautifulSoup(html, "html.parser")
    rx = re.compile(pattern)
    excluded = {e.rstrip("/") for e in exclude}
    seen: dict[str, str] = {}
    for a in soup.find_all("a", href=True):
        href = a["href"].split("?")[0].split("#")[0]
        path = urlsplit(href).path if href.startswith("http") else href
        if not rx.match(path) or path.rstrip("/") in excluded:
            continue
        url = normalize_url(urljoin(base_url, path))
        text = clean_text(a.get_text(" ", strip=True), 300)
        if url not in seen or (text and not seen[url]):
            seen[url] = text
    return list(seen.items())


def parse_detail(html: str) -> dict:
    soup = BeautifulSoup(html, "html.parser")

    def meta(prop: str) -> str | None:
        tag = soup.find("meta", attrs={"property": prop}) or soup.find("meta", attrs={"name": prop})
        return tag.get("content") if tag else None

    paras = [clean_text(p.get_text(" ", strip=True)) for p in soup.find_all("p")]
    body = " ".join(p for p in paras if len(p) > 60)[:1500]
    return {
        "title": clean_text(meta("og:title") or (soup.title.string if soup.title and soup.title.string else ""), 300),
        "published_at": _parse_iso(meta("article:published_time")),
        "summary": body or clean_text(meta("og:description") or "", 600),
    }


def collect_html_list(source: SourceConfig, *, user_agent: str, timeout: float, retries: int,
                      known_urls: set[str] | None = None) -> list[RawArticle]:
    if not robots_allows(source.url, user_agent, timeout=timeout):
        raise RobotsDisallowed(f"robots.txt가 {source.url} 수집을 허용하지 않음")
    cfg = source.html or {}
    r = fetch(source.url, user_agent=user_agent, timeout=timeout, retries=retries)
    links = extract_links(r.text, source.url, cfg.get("link_pattern", ".*"), cfg.get("exclude_paths", []))
    known_urls = known_urls or set()
    items: list[RawArticle] = []
    for url, anchor_text in links:
        if url in known_urls:
            continue
        if len(items) >= source.max_items:
            break
        title, summary, published = anchor_text, "", None
        if cfg.get("fetch_detail", True):
            try:
                d = parse_detail(fetch(url, user_agent=user_agent, timeout=timeout, retries=retries).text)
                title, summary, published = d["title"] or title, d["summary"], d["published_at"]
            except Exception as e:
                log.warning("상세 페이지 실패 %s: %s", url, e)
        if not title:
            continue
        items.append(RawArticle(
            source_id=source.id, source_name=source.name, group=source.group, url=url,
            title=title, summary=summary, published_at=published, published_uncertain=published is None,
            default_category=source.default_category, prefilter=source.prefilter,
        ))
    return items
