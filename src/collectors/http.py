"""재시도가 붙은 HTTP GET."""
from __future__ import annotations

import logging
import time

import httpx

log = logging.getLogger(__name__)
RETRY_STATUS = {429, 500, 502, 503, 504}


def fetch(url: str, *, user_agent: str, timeout: float = 20.0, retries: int = 3,
          backoff: float = 2.0, client: httpx.Client | None = None) -> httpx.Response:
    """GET 요청. 네트워크 오류·5xx·429는 지수 백오프로 재시도, 4xx는 즉시 실패."""
    headers = {"User-Agent": user_agent, "Accept": "application/rss+xml, application/atom+xml, application/xml, text/html;q=0.8, */*;q=0.5"}
    own = client is None
    client = client or httpx.Client(timeout=timeout, follow_redirects=True)
    last_exc: Exception | None = None
    try:
        for attempt in range(1, retries + 1):
            try:
                r = client.get(url, headers=headers)
                if r.status_code in RETRY_STATUS and attempt < retries:
                    log.warning("HTTP %s %s (시도 %d/%d)", r.status_code, url, attempt, retries)
                    time.sleep(backoff ** attempt)
                    continue
                r.raise_for_status()
                return r
            except httpx.HTTPStatusError:
                raise
            except httpx.HTTPError as e:
                last_exc = e
                log.warning("네트워크 오류 %s: %s (시도 %d/%d)", url, e, attempt, retries)
                if attempt < retries:
                    time.sleep(backoff ** attempt)
        raise last_exc or RuntimeError(f"fetch failed: {url}")
    finally:
        if own:
            client.close()
