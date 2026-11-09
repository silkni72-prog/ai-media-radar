"""ntfy 발송. https://docs.ntfy.sh/publish/ 의 JSON 발행 방식을 쓴다.

- 같은 (kind, dedup_key)는 한 번만 성공 처리된다 (DB 유일 제약).
- 실패하면 제한된 횟수만 재시도하고 결과를 DB에 남긴다.
- NTFY_DRY_RUN=true(기본값)면 실제로 보내지 않고 내용을 로그·DB에만 남긴다.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field

import httpx

log = logging.getLogger(__name__)

MAX_MESSAGE_CHARS = 900  # 푸시 본문은 짧게 (ntfy 메시지 한도 4KB보다 훨씬 작게)


def mask_topic(topic: str) -> str:
    """공개 ntfy 서버에서는 토픽 이름을 아는 사람 누구나 구독할 수 있다 → 로그에는 앞부분만 남긴다."""
    return (topic[:12] + "…") if len(topic) > 12 else "…"


@dataclass
class Push:
    topic: str
    title: str
    message: str
    priority: int = 3  # 1~5, 3=default
    tags: list[str] = field(default_factory=list)
    click: str = ""
    actions: list[dict] = field(default_factory=list)

    def payload(self) -> dict:
        msg = self.message if len(self.message) <= MAX_MESSAGE_CHARS else self.message[: MAX_MESSAGE_CHARS - 1] + "…"
        body = {"topic": self.topic, "title": self.title[:250], "message": msg, "priority": self.priority,
                "tags": self.tags, "markdown": False}
        if self.click:
            body["click"] = self.click
        if self.actions:
            body["actions"] = self.actions[:3]
        return body


@dataclass
class SendResult:
    status: str  # sent | failed | dry_run | duplicate | no_topic
    attempts: int = 0
    error: str = ""
    message_id: str = ""


class NtfyNotifier:
    def __init__(self, settings, repo, *, client: httpx.Client | None = None, sleep=time.sleep):
        self.st = settings
        self.repo = repo
        self.client = client or httpx.Client(timeout=15)
        self.sleep = sleep

    def _post(self, push: Push) -> httpx.Response:
        headers = {"Authorization": f"Bearer {self.st.ntfy_token}"} if self.st.ntfy_token else {}
        # 토픽을 URL에 넣지 않고 base URL로 JSON 발행 (한글 제목도 헤더 인코딩 문제 없음)
        return self.client.post(self.st.ntfy_base_url, json=push.payload(), headers=headers)

    def send(self, kind: str, dedup_key: str, push: Push, *, force: bool = False) -> SendResult:
        if not push.topic:
            log.warning("[%s] ntfy 토픽이 설정되지 않아 건너뜀 (환경변수 확인)", kind)
            return SendResult("no_topic")
        row = self.repo.claim_notification(kind, dedup_key + (f"#force{time.time_ns()}" if force else ""),
                                           push.topic, push.title)
        if row is None:
            log.info("[%s] 이미 보낸 알림이라 건너뜀: %s", kind, dedup_key)
            return SendResult("duplicate")

        if self.st.ntfy_dry_run:
            log.info("[DRY-RUN ntfy:%s] %s\n%s\nclick=%s", mask_topic(push.topic), push.title, push.message, push.click)
            self.repo.finish_notification(row, status="dry_run", attempts=0)
            return SendResult("dry_run")

        attempts, err = 0, ""
        for attempt in range(1, self.st.ntfy_max_retries + 1):
            attempts = attempt
            try:
                r = self._post(push)
                if r.status_code < 300:
                    mid = ""
                    try:
                        mid = r.json().get("id", "")
                    except ValueError:
                        pass
                    self.repo.finish_notification(row, status="sent", attempts=attempts, message_id=mid)
                    log.info("[%s] ntfy 발송 성공 (%s, %d회 시도)", kind, mask_topic(push.topic), attempts)
                    return SendResult("sent", attempts, message_id=mid)
                err = f"HTTP {r.status_code}: {r.text[:200]}"
                if 400 <= r.status_code < 500 and r.status_code != 429:
                    break  # 인증·형식 오류는 재시도해도 같다
            except httpx.HTTPError as e:
                err = f"{type(e).__name__}: {e}"
            log.warning("[%s] ntfy 발송 실패(%d/%d): %s", kind, attempt, self.st.ntfy_max_retries, err)
            if attempt < self.st.ntfy_max_retries:
                self.sleep(2 ** attempt)
        self.repo.finish_notification(row, status="failed", attempts=attempts, error=err)
        return SendResult("failed", attempts, err)
