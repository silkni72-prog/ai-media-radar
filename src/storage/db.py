"""SQLAlchemy 테이블 정의. DATABASE_URL만 바꾸면 PostgreSQL/Supabase로 옮길 수 있다."""
from __future__ import annotations

from datetime import datetime
from pathlib import Path

from sqlalchemy import (JSON, Boolean, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint,
                        create_engine)
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship, sessionmaker


class Base(DeclarativeBase):
    pass


class ArticleRow(Base):
    __tablename__ = "articles"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    url_hash: Mapped[str] = mapped_column(String(32), unique=True, index=True)
    url: Mapped[str] = mapped_column(Text)
    source_id: Mapped[str] = mapped_column(String(64), index=True)
    source_name: Mapped[str] = mapped_column(String(128))
    group: Mapped[str] = mapped_column(String(32), index=True)
    title: Mapped[str] = mapped_column(Text)
    title_fp: Mapped[str] = mapped_column(Text, default="")
    summary: Mapped[str] = mapped_column(Text, default="")
    author: Mapped[str] = mapped_column(String(256), default="")
    default_category: Mapped[str] = mapped_column(String(8), default="AI")
    published_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, index=True)  # UTC
    published_uncertain: Mapped[bool] = mapped_column(Boolean, default=False)
    collected_at: Mapped[datetime] = mapped_column(DateTime, index=True)  # UTC
    prefilter_passed: Mapped[bool] = mapped_column(Boolean, default=True)
    duplicate_of: Mapped[int | None] = mapped_column(ForeignKey("articles.id"), nullable=True)
    analysis_status: Mapped[str] = mapped_column(String(16), default="pending", index=True)  # pending|done|skipped|error

    analysis: Mapped["AnalysisRow | None"] = relationship(back_populates="article", uselist=False)


class AnalysisRow(Base):
    __tablename__ = "analyses"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    article_id: Mapped[int] = mapped_column(ForeignKey("articles.id"), unique=True)
    relevant: Mapped[bool] = mapped_column(Boolean, default=True)
    category: Mapped[str] = mapped_column(String(8))
    subcategory: Mapped[str] = mapped_column(String(64), default="")
    title_ko: Mapped[str] = mapped_column(Text)
    summary_ko: Mapped[str] = mapped_column(Text)
    why_it_matters: Mapped[str] = mapped_column(Text, default="")
    newsroom_impact: Mapped[str] = mapped_column(Text, default="")
    reporting_angle: Mapped[str] = mapped_column(Text, default="")
    importance_score: Mapped[int] = mapped_column(Integer, index=True)
    scores: Mapped[dict] = mapped_column(JSON, default=dict)
    confidence: Mapped[str] = mapped_column(String(8))
    confidence_note: Mapped[str] = mapped_column(Text, default="")
    method: Mapped[str] = mapped_column(String(16))
    model: Mapped[str] = mapped_column(String(64), default="")
    validation_flags: Mapped[list] = mapped_column(JSON, default=list)
    issue_key: Mapped[str] = mapped_column(String(64), default="", index=True)
    analyzed_at: Mapped[datetime] = mapped_column(DateTime)

    article: Mapped[ArticleRow] = relationship(back_populates="analysis")


class NotificationRow(Base):
    """푸시 발송 이력. (kind, dedup_key)가 유일해서 같은 알림은 두 번 성공 처리되지 않는다."""

    __tablename__ = "notifications"
    __table_args__ = (UniqueConstraint("kind", "dedup_key", name="uq_notification_kind_key"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    kind: Mapped[str] = mapped_column(String(16))  # daily | wired | breaking | test
    dedup_key: Mapped[str] = mapped_column(String(128))
    topic: Mapped[str] = mapped_column(String(128), default="")
    title: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(16), default="pending")  # pending|sent|failed|dry_run
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    last_error: Mapped[str] = mapped_column(Text, default="")
    ntfy_message_id: Mapped[str] = mapped_column(String(64), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class FetchLogRow(Base):
    __tablename__ = "fetch_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    source_id: Mapped[str] = mapped_column(String(64), index=True)
    started_at: Mapped[datetime] = mapped_column(DateTime)
    ok: Mapped[bool] = mapped_column(Boolean)
    n_items: Mapped[int] = mapped_column(Integer, default=0)
    n_new: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[str] = mapped_column(Text, default="")


def make_engine(database_url: str) -> Engine:
    if database_url.startswith("sqlite:///"):
        Path(database_url.removeprefix("sqlite:///")).parent.mkdir(parents=True, exist_ok=True)
        engine = create_engine(database_url, connect_args={"timeout": 30})
    else:
        engine = create_engine(database_url, pool_pre_ping=True)
    Base.metadata.create_all(engine)
    return engine


def make_session_factory(database_url: str):
    return sessionmaker(bind=make_engine(database_url), expire_on_commit=False)
