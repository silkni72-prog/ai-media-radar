"""URL 정규화, 텍스트 정리, 제목 지문 — 중복 제거의 기준."""
from __future__ import annotations

import hashlib
import html
import re
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from bs4 import BeautifulSoup

TRACKING_PREFIXES = ("utm_", "mc_", "fbclid", "gclid", "ocid", "cmpid", "ref", "src", "sref", "mbid", "rss", "feed")


def normalize_url(url: str) -> str:
    """추적 파라미터·프래그먼트·끝 슬래시를 없애고 스킴/호스트를 소문자로."""
    url = (url or "").strip()
    if not url:
        return ""
    parts = urlsplit(url)
    scheme = "https" if parts.scheme in ("http", "https", "") else parts.scheme
    host = parts.netloc.lower()
    if host.endswith(":443") or host.endswith(":80"):
        host = host.rsplit(":", 1)[0]
    path = re.sub(r"/{2,}", "/", parts.path or "/")
    if len(path) > 1:
        path = path.rstrip("/")
    query_items = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=False)
                   if not k.lower().startswith(TRACKING_PREFIXES)]
    # 기사 ID가 쿼리에 들어가는 국내 CMS(idxno 등) 때문에 추적용이 아닌 파라미터는 유지
    query = urlencode(sorted(query_items))
    if host.endswith("arxiv.org"):
        host = "arxiv.org"
        path = re.sub(r"v\d+$", "", path)  # 버전 차이는 같은 논문
    return urlunsplit((scheme, host, path, query, ""))


def url_hash(url: str) -> str:
    return hashlib.sha256(normalize_url(url).encode()).hexdigest()[:32]


def clean_text(raw: str, limit: int = 1500) -> str:
    if not raw:
        return ""
    text = BeautifulSoup(raw, "html.parser").get_text(" ", strip=True) if "<" in raw else raw
    text = html.unescape(re.sub(r"\s+", " ", text)).strip()
    return text[:limit]


_STOP = {"the", "a", "an", "of", "to", "in", "on", "for", "and", "with", "is", "are", "its", "it", "at", "by", "as", "from", "how", "why", "what"}


def title_tokens(title: str) -> set[str]:
    t = re.sub(r"[\[\(【].*?[\]\)】]", " ", title.lower())  # [속보] 같은 말머리 제거
    out: set[str] = set()
    for x in re.findall(r"[a-z0-9]+|[가-힣]{2,}", t):
        if x in _STOP or len(x) < 2:
            continue
        if "가" <= x[0] <= "힣" and len(x) > 2:
            # 한국어는 띄어쓰기·조사 차이('매각공고' vs '매각 공고')를 흡수하려고 2글자 단위로 쪼갠다
            out.update(x[i:i + 2] for i in range(len(x) - 1))
        else:
            out.add(x)
    return out


def title_fingerprint(title: str) -> str:
    return " ".join(sorted(title_tokens(title)))


def jaccard(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)
