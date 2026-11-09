"""수집 → 정제 → 분석 → 저장 흐름과 WIRED 독립 모니터."""
from __future__ import annotations

import logging
from datetime import timedelta

from sqlalchemy import select

from .analysis.analyzer import analyze, fmt_kst
from .analysis.clustering import assign_issue_key, tokens_for
from .analysis.keywords import prefilter
from .analysis.llm import LLMClient
from .collectors.registry import collect_source, load_sources, sources_by_group
from .models import Group
from .notifications.ntfy import NtfyNotifier, Push
from .storage.db import AnalysisRow, ArticleRow
from .storage.repository import Repository, utcnow

log = logging.getLogger(__name__)


def collect(repo: Repository, settings, *, wired: bool, only: list[str] | None = None) -> dict:
    """출처별로 수집해서 새 기사를 저장. 한 출처가 실패해도 나머지는 계속한다."""
    sources = sources_by_group(load_sources(settings.sources_file), wired=wired)
    if only:
        sources = [s for s in sources if s.id in only]
    cutoff = utcnow() - timedelta(hours=settings.collect_lookback_hours)
    report = {"ok": [], "failed": [], "new": 0}
    for src in sources:
        started = utcnow()
        try:
            known = repo.known_urls(src.id) if src.type == "html_list" else None
            items = collect_source(src, settings, known_urls=known)
            # 오래된 기사는 저장하지 않는다 (발행 시각을 모르는 기사는 일단 받는다)
            fresh = [i for i in items if i.published_at is None or i.published_at >= cutoff]
            new_rows = repo.add_articles(fresh, prefilter_fn=prefilter)
            repo.log_fetch(src.id, started, True, len(items), len(new_rows))
            repo.s.commit()
            report["ok"].append((src.id, len(items), len(new_rows)))
            report["new"] += len(new_rows)
            log.info("수집 %-24s 항목 %3d / 신규 %3d", src.id, len(items), len(new_rows))
        except Exception as e:  # 출처 하나의 실패가 전체를 멈추지 않게
            repo.s.rollback()
            repo.log_fetch(src.id, started, False, error=f"{type(e).__name__}: {e}")
            repo.s.commit()
            report["failed"].append((src.id, f"{type(e).__name__}: {e}"[:200]))
            log.error("수집 실패 %s: %s", src.id, e)
    return report


def _recent_issue_candidates(repo: Repository, hours: int = 72) -> list[tuple[str, set[str]]]:
    rows = repo.s.execute(
        select(AnalysisRow.issue_key, ArticleRow.title, AnalysisRow.title_ko)
        .join(ArticleRow, ArticleRow.id == AnalysisRow.article_id)
        .where(AnalysisRow.analyzed_at >= utcnow() - timedelta(hours=hours))).all()
    return [(k, tokens_for(t, tk)) for k, t, tk in rows if k]


def _round_robin(rows: list[ArticleRow], limit: int) -> list[ArticleRow]:
    """출처별로 돌아가며 1건씩 뽑는다. 기사량이 많은 국내 IT 매체가 상한을 독차지해
    해외·언론 전문지 기사가 분석에서 밀리는 것을 막는다. 각 출처 안에서는 최신순 유지."""
    by_src: dict[str, list[ArticleRow]] = {}
    for r in rows:
        by_src.setdefault(r.source_id, []).append(r)
    queues = list(by_src.values())
    out: list[ArticleRow] = []
    while queues and len(out) < limit:
        for q in list(queues):
            out.append(q.pop(0))
            if not q:
                queues.remove(q)
            if len(out) >= limit:
                break
    return out


def analyze_pending(repo: Repository, settings, client: LLMClient | None, *,
                    groups: list[str] | None = None, limit: int | None = None) -> list[ArticleRow]:
    # 비용 상한은 LLM 호출에만 적용. 규칙 기반 분석은 비용이 없으므로 넉넉히 처리한다.
    limit = limit or (settings.llm_max_articles_per_run if client else 1000)
    since = utcnow() - timedelta(hours=settings.collect_lookback_hours)
    rows = _round_robin(repo.pending_articles(groups=groups, limit=limit * 5, since=since), limit)
    candidates = _recent_issue_candidates(repo)
    done = []
    now = utcnow()
    for row in rows:
        try:
            a = analyze(client=client, title=row.title, summary=row.summary, url=row.url,
                        source_name=row.source_name, published_at=row.published_at,
                        published_uncertain=row.published_uncertain,
                        default_category=row.default_category, group=row.group, now=now)
            toks = tokens_for(row.title, a.title_ko)
            key = assign_issue_key(toks, candidates, fallback_key=row.url_hash)
            candidates.append((key, toks))
            repo.save_analysis(row, a, issue_key=key)
            repo.s.commit()
            done.append(row)
        except Exception as e:
            repo.s.rollback()
            row.analysis_status = "error"
            repo.s.commit()
            log.exception("분석 실패 %s: %s", row.url, e)
    log.info("분석 완료 %d건 (방식: %s)", len(done), "LLM" if client else "규칙 기반")
    return done


# ── 알림 ─────────────────────────────────────────────────────────────

def _alert_allowed(row: ArticleRow, settings) -> bool:
    """규칙 기반 점수는 오탐이 많아 즉시 푸시에 쓰지 않는다."""
    return row.analysis is not None and row.analysis.relevant and row.analysis.method == "llm"


def wired_push(row: ArticleRow, topic: str) -> Push:
    a = row.analysis
    when = fmt_kst(row.published_at) + (" (발행 시각 미확인)" if row.published_uncertain else "")
    msg = f"{a.summary_ko}\n\n왜 중요한가: {a.why_it_matters}\n{when} · 중요도 {a.importance_score}"
    return Push(topic=topic, title=f"[WIRED] {a.title_ko}", message=msg, priority=4,
                tags=["newspaper", "wired"], click=row.url,
                actions=[{"action": "view", "label": "원문 열기", "url": row.url}])


def breaking_push(row: ArticleRow, topic: str) -> Push:
    a = row.analysis
    msg = f"{a.summary_ko}\n\n출처: {row.source_name} · {fmt_kst(row.published_at)} · 중요도 {a.importance_score}"
    return Push(topic=topic, title=f"[속보] {a.title_ko}", message=msg, priority=5,
                tags=["rotating_light"], click=row.url,
                actions=[{"action": "view", "label": "원문 열기", "url": row.url}])


def send_alerts(rows: list[ArticleRow], repo: Repository, settings, notifier: NtfyNotifier, *,
                wired: bool) -> dict:
    out = {"wired": 0, "breaking": 0}
    for row in rows:
        if not _alert_allowed(row, settings):
            continue
        a = row.analysis
        if wired and a.importance_score >= settings.wired_alert_threshold:
            r = notifier.send("wired", row.url_hash, wired_push(row, settings.ntfy_wired_topic))
            out["wired"] += r.status in ("sent", "dry_run")
        if a.importance_score >= settings.breaking_threshold and (wired or settings.breaking_all_sources):
            # 같은 사건(issue_key)은 한 번만
            r = notifier.send("breaking", a.issue_key or row.url_hash, breaking_push(row, settings.ntfy_alert_topic))
            out["breaking"] += r.status in ("sent", "dry_run")
        repo.s.commit()
    return out


def run_wired(repo: Repository, settings, client, notifier: NtfyNotifier) -> dict:
    report = collect(repo, settings, wired=True)
    rows = analyze_pending(repo, settings, client, groups=[Group.WIRED.value])
    report["analyzed"] = len(rows)
    report["alerts"] = send_alerts(rows, repo, settings, notifier, wired=True)
    return report


def run_hourly(repo: Repository, settings, client, notifier: NtfyNotifier) -> dict:
    report = collect(repo, settings, wired=False)
    groups = [g.value for g in Group if g != Group.WIRED]
    rows = analyze_pending(repo, settings, client, groups=groups)
    report["analyzed"] = len(rows)
    report["alerts"] = send_alerts(rows, repo, settings, notifier, wired=False)
    return report
