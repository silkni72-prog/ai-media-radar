"""DB 접근을 한곳에 모은 저장소 계층. 다른 모듈은 SQL을 직접 다루지 않는다."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, joinedload

from ..analysis.keywords import is_noise
from ..collectors.normalize import title_fingerprint, url_hash
from ..models import Analysis, RawArticle
from .db import AnalysisRow, ArticleRow, FetchLogRow, NotificationRow


def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


class Repository:
    def __init__(self, session: Session):
        self.s = session

    # ── 기사 ────────────────────────────────────────────
    def known_urls(self, source_id: str) -> set[str]:
        return set(self.s.scalars(select(ArticleRow.url).where(ArticleRow.source_id == source_id)))

    def add_articles(self, items: list[RawArticle], *, prefilter_fn=None) -> list[ArticleRow]:
        """새 기사만 저장해서 돌려준다. URL 해시가 같으면 이미 있는 기사로 본다.
        같은 출처에서 제목 지문이 같은 기사(재발행·URL 변경)는 duplicate_of로 표시한다."""
        new_rows: list[ArticleRow] = []
        now = utcnow()
        seen_in_batch: set[str] = set()
        for it in items:
            h = url_hash(it.url)
            if h in seen_in_batch or self.s.scalar(select(ArticleRow.id).where(ArticleRow.url_hash == h)):
                continue
            seen_in_batch.add(h)
            fp = title_fingerprint(it.title)
            dup_id = None
            if fp:
                dup_id = self.s.scalar(select(ArticleRow.id).where(
                    ArticleRow.source_id == it.source_id, ArticleRow.title_fp == fp,
                    ArticleRow.collected_at >= now - timedelta(days=7)).limit(1))
            passed = not is_noise(it.title) and (True if (prefilter_fn is None or not it.prefilter) else prefilter_fn(it))
            row = ArticleRow(
                url_hash=h, url=it.url, source_id=it.source_id, source_name=it.source_name,
                group=it.group.value, title=it.title, title_fp=fp, summary=it.summary, author=it.author,
                default_category=it.default_category, published_at=it.published_at,
                published_uncertain=it.published_uncertain, collected_at=now,
                prefilter_passed=passed, duplicate_of=dup_id,
                analysis_status="pending" if (passed and dup_id is None) else "skipped",
            )
            self.s.add(row)
            new_rows.append(row)
        self.s.flush()
        return new_rows

    def pending_articles(self, *, groups: list[str] | None = None, limit: int = 40,
                         since: datetime | None = None) -> list[ArticleRow]:
        q = select(ArticleRow).where(ArticleRow.analysis_status == "pending")
        if groups:
            q = q.where(ArticleRow.group.in_(groups))
        if since:
            q = q.where(or_(ArticleRow.published_at >= since, ArticleRow.published_at.is_(None)))
        q = q.order_by(ArticleRow.published_at.desc().nullslast(), ArticleRow.id.desc()).limit(limit)
        return list(self.s.scalars(q))

    def mark_status(self, row: ArticleRow, status: str) -> None:
        row.analysis_status = status

    def save_analysis(self, row: ArticleRow, a: Analysis, issue_key: str = "") -> AnalysisRow:
        ar = row.analysis or AnalysisRow(article_id=row.id)
        for field in ("relevant", "category", "subcategory", "title_ko", "summary_ko", "why_it_matters",
                      "newsroom_impact", "reporting_angle", "importance_score", "confidence",
                      "confidence_note", "method", "model", "validation_flags"):
            setattr(ar, field, getattr(a, field))
        ar.scores = a.scores.model_dump()
        ar.issue_key = issue_key
        ar.analyzed_at = utcnow()
        self.s.add(ar)
        row.analysis = ar
        row.analysis_status = "done"
        self.s.flush()
        return ar

    def analyzed_in_window(self, start: datetime, end: datetime, *, groups: list[str] | None = None,
                           relevant_only: bool = True) -> list[ArticleRow]:
        """창 안에 발행된 기사. 발행 시각을 모르는 기사는 수집 시각으로 판단한다."""
        q = (select(ArticleRow).join(AnalysisRow).options(joinedload(ArticleRow.analysis))
             .where(ArticleRow.duplicate_of.is_(None))
             .where(or_(
                 (ArticleRow.published_at >= start) & (ArticleRow.published_at < end),
                 ArticleRow.published_at.is_(None) & (ArticleRow.collected_at >= start) & (ArticleRow.collected_at < end),
             )))
        if groups:
            q = q.where(ArticleRow.group.in_(groups))
        if relevant_only:
            q = q.where(AnalysisRow.relevant.is_(True))
        return list(self.s.scalars(q).unique())

    # ── 알림 ────────────────────────────────────────────
    def notification_sent(self, kind: str, key: str) -> bool:
        return bool(self.s.scalar(select(NotificationRow.id).where(
            NotificationRow.kind == kind, NotificationRow.dedup_key == key,
            NotificationRow.status.in_(["sent", "dry_run"]))))

    def claim_notification(self, kind: str, key: str, topic: str, title: str) -> NotificationRow | None:
        """발송 권한을 얻는다. 이미 성공한 알림이면 None. 실패 이력이 있으면 재사용한다."""
        row = self.s.scalar(select(NotificationRow).where(NotificationRow.kind == kind, NotificationRow.dedup_key == key))
        if row is not None:
            return None if row.status in ("sent", "dry_run") else row
        row = NotificationRow(kind=kind, dedup_key=key, topic=topic, title=title, created_at=utcnow())
        self.s.add(row)
        try:
            self.s.flush()
        except IntegrityError:  # 동시 실행에서 다른 프로세스가 먼저 잡은 경우
            self.s.rollback()
            return None
        return row

    def finish_notification(self, row: NotificationRow, *, status: str, attempts: int,
                            error: str = "", message_id: str = "") -> None:
        row.status, row.attempts, row.last_error, row.ntfy_message_id = status, attempts, error[:1000], message_id
        if status in ("sent", "dry_run"):
            row.sent_at = utcnow()
        self.s.flush()

    def issue_alerted(self, issue_key: str) -> bool:
        return self.notification_sent("breaking", issue_key)

    # ── 로그 ────────────────────────────────────────────
    def log_fetch(self, source_id: str, started: datetime, ok: bool, n_items: int = 0, n_new: int = 0,
                  error: str = "") -> None:
        self.s.add(FetchLogRow(source_id=source_id, started_at=started, ok=ok, n_items=n_items,
                               n_new=n_new, error=error[:2000]))

    def recent_fetch_failures(self, hours: int = 24) -> list[FetchLogRow]:
        return list(self.s.scalars(select(FetchLogRow).where(
            FetchLogRow.ok.is_(False), FetchLogRow.started_at >= utcnow() - timedelta(hours=hours))
            .order_by(FetchLogRow.started_at.desc())))
