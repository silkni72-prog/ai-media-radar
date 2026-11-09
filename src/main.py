"""AI × MEDIA DAILY RADAR — 명령행 진입점.

    python -m src.main sources-check        # 출처 접근 확인
    python -m src.main hourly               # 전체 뉴스 수집·분석 (1시간 간격)
    python -m src.main wired                # WIRED 모니터 (30분 간격)
    python -m src.main briefing             # 일일 브리핑 생성·발송 (09:00 KST)
    python -m src.main notify-test daily    # ntfy 테스트 알림
    python -m src.main status               # 최근 수집 실패·알림 이력
"""
from __future__ import annotations

import argparse
import logging
import sys
import time
from datetime import datetime, timedelta, timezone
from logging.handlers import RotatingFileHandler

from sqlalchemy import func, select

from .analysis.llm import get_client
from .briefing import render
from .briefing.builder import build_briefing
from .collectors.registry import collect_source, load_sources
from .config import KST, ROOT, get_settings
from .notifications.ntfy import NtfyNotifier, Push
from .pipeline import run_hourly, run_wired
from .storage.db import AnalysisRow, ArticleRow, NotificationRow, make_session_factory
from .storage.repository import Repository

log = logging.getLogger("radar")


def setup_logging(verbose: bool = False) -> None:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except (AttributeError, ValueError):
            pass
    (ROOT / "data" / "logs").mkdir(parents=True, exist_ok=True)
    fmt = logging.Formatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s")
    root = logging.getLogger()
    root.setLevel(logging.DEBUG if verbose else logging.INFO)
    sh = logging.StreamHandler(sys.stdout)
    sh.setFormatter(fmt)
    fh = RotatingFileHandler(ROOT / "data" / "logs" / "radar.log", maxBytes=2_000_000, backupCount=3, encoding="utf-8")
    fh.setFormatter(fmt)
    root.handlers[:] = [sh, fh]
    for noisy in ("httpx", "httpcore", "anthropic", "openai"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def _ctx():
    st = get_settings()
    session = make_session_factory(st.database_url)()
    repo = Repository(session)
    return st, repo, get_client(st), NtfyNotifier(st, repo)


def wait_until_kst(hhmm: str, max_wait_min: int = 40, now_fn=lambda: datetime.now(KST), sleep=time.sleep) -> float:
    """GitHub Actions 예약이 일찍 깨어난 경우 지정 시각(KST)까지 기다린다. 기다린 초를 돌려준다."""
    h, m = map(int, hhmm.split(":"))
    now = now_fn()
    target = now.replace(hour=h, minute=m, second=0, microsecond=0)
    wait = (target - now).total_seconds()
    if 0 < wait <= max_wait_min * 60:
        log.info("%s KST까지 %.0f초 대기", hhmm, wait)
        sleep(wait)
        return wait
    return 0.0


def cmd_sources_check(args) -> int:
    st = get_settings()
    sources = load_sources(st.sources_file, include_disabled=True)
    rows = ["| id | 이름 | 상태 | 항목 수 | 최신 발행(KST) | 비고 |", "|---|---|---|---|---|---|"]
    ok = fail = 0
    for s in sources:
        if not s.enabled:
            rows.append(f"| {s.id} | {s.name} | 비활성 | - | - | {s.verified} |")
            continue
        try:
            items = collect_source(s, st, known_urls=set())
            latest = max((i.published_at for i in items if i.published_at), default=None)
            latest_s = latest.replace(tzinfo=timezone.utc).astimezone(KST).strftime("%Y-%m-%d %H:%M") if latest else "확인 불가"
            rows.append(f"| {s.id} | {s.name} | ✅ 성공 | {len(items)} | {latest_s} | |")
            ok += 1
        except Exception as e:
            rows.append(f"| {s.id} | {s.name} | ❌ 실패 | 0 | - | {type(e).__name__}: {str(e)[:80]} |")
            fail += 1
    report = "\n".join([f"# 출처 점검 ({datetime.now(KST):%Y-%m-%d %H:%M} KST)", "", f"성공 {ok} / 실패 {fail} / 비활성 {len(sources) - ok - fail}", ""] + rows)
    st.output_dir.mkdir(parents=True, exist_ok=True)
    (st.output_dir / "sources-report.md").write_text(report, encoding="utf-8")
    print(report)
    return 0 if fail == 0 else 1


def cmd_hourly(args) -> int:
    st, repo, client, notifier = _ctx()
    rep = run_hourly(repo, st, client, notifier)
    log.info("hourly 완료: 신규 %d, 분석 %d, 실패 출처 %s, 알림 %s", rep["new"], rep["analyzed"],
             [f for f, _ in rep["failed"]], rep["alerts"])
    return 0


def cmd_wired(args) -> int:
    st, repo, client, notifier = _ctx()
    rep = run_wired(repo, st, client, notifier)
    log.info("wired 완료: 신규 %d, 분석 %d, 실패 %s, 알림 %s", rep["new"], rep["analyzed"],
             [f for f, _ in rep["failed"]], rep["alerts"])
    return 0 if not rep["failed"] or rep["ok"] else 1


def cmd_briefing(args) -> int:
    if args.wait_until:
        wait_until_kst(args.wait_until)
        h, m = map(int, args.wait_until.split(":"))
        now = datetime.now(KST)
        if (now.hour, now.minute) < (h, m) and not args.force:
            log.info("아직 %s KST 전이라 실행하지 않음 (너무 이른 예약 실행)", args.wait_until)
            return 0
    st, repo, client, notifier = _ctx()
    date_kst = datetime.now(KST).strftime("%Y-%m-%d")
    if not args.force and not args.no_send and repo.notification_sent("daily", date_kst):
        log.info("%s 브리핑은 이미 발송됨 → 건너뜀 (--force로 재발송)", date_kst)
        return 0
    if not args.skip_collect:
        run_hourly(repo, st, client, notifier)
        run_wired(repo, st, client, notifier)  # WIRED 섹션용 — 이미 보낸 알림은 다시 보내지 않는다
    b = build_briefing(repo, client)
    md_path, html_path = render.save(b, st.output_dir)
    log.info("브리핑 저장: %s, %s (AI %d / 언론 %d / WIRED %d)", md_path, html_path,
             len(b.part_ai), len(b.part_media), len(b.part_wired))
    if args.no_send:
        return 0
    push = render.daily_push(b, st.ntfy_daily_topic, st.briefing_public_base_url)
    r = notifier.send("daily", b.date_kst, push, force=args.force)
    repo.s.commit()
    log.info("일일 브리핑 알림: %s", r.status)
    return 1 if r.status == "failed" else 0


def cmd_notify_test(args) -> int:
    st, repo, _, notifier = _ctx()
    topic = {"daily": st.ntfy_daily_topic, "wired": st.ntfy_wired_topic, "alert": st.ntfy_alert_topic}[args.target]
    now = datetime.now(KST).strftime("%Y-%m-%d %H:%M:%S")
    push = Push(topic=topic, title=f"[테스트] AI×MEDIA RADAR ({args.target})",
                message=f"ntfy 연결 테스트입니다. {now} KST", priority=2, tags=["white_check_mark"],
                click="https://docs.ntfy.sh/")
    r = notifier.send("test", f"{args.target}-{now}", push)
    repo.s.commit()
    print(f"결과: {r.status} (시도 {r.attempts}회) {r.error}")
    if r.status == "dry_run":
        print("※ NTFY_DRY_RUN=true 상태라 실제로 보내지 않았습니다. 실제 발송은 .env에서 NTFY_DRY_RUN=false로 바꾸세요.")
    return 0 if r.status in ("sent", "dry_run") else 1


def cmd_status(args) -> int:
    st, repo, _, _ = _ctx()
    s = repo.s
    since = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(hours=24)
    print(f"DB: {st.database_url}")
    print(f"기사 전체 {s.scalar(select(func.count(ArticleRow.id)))}건, 최근 24시간 수집 "
          f"{s.scalar(select(func.count(ArticleRow.id)).where(ArticleRow.collected_at >= since))}건")
    for status, n in s.execute(select(ArticleRow.analysis_status, func.count()).group_by(ArticleRow.analysis_status)):
        print(f"  분석 상태 {status}: {n}")
    print(f"분석 완료 {s.scalar(select(func.count(AnalysisRow.id)))}건")
    print("\n최근 24시간 수집 실패:")
    for f in repo.recent_fetch_failures(24)[:20]:
        print(f"  {f.started_at:%m-%d %H:%M} UTC {f.source_id}: {f.error[:120]}")
    print("\n최근 알림 10건:")
    for n in s.scalars(select(NotificationRow).order_by(NotificationRow.id.desc()).limit(10)):
        print(f"  {n.created_at:%m-%d %H:%M} {n.kind:8} {n.status:8} 시도{n.attempts} {n.title[:50]} {n.last_error[:60]}")
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="radar", description="AI × MEDIA DAILY RADAR")
    p.add_argument("-v", "--verbose", action="store_true")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("sources-check").set_defaults(fn=cmd_sources_check)
    sub.add_parser("hourly").set_defaults(fn=cmd_hourly)
    sub.add_parser("wired").set_defaults(fn=cmd_wired)
    b = sub.add_parser("briefing")
    b.add_argument("--no-send", action="store_true", help="파일만 만들고 알림은 보내지 않음")
    b.add_argument("--force", action="store_true", help="이미 보낸 날짜라도 다시 생성·발송")
    b.add_argument("--skip-collect", action="store_true", help="수집 없이 DB에 있는 기사로만 생성")
    b.add_argument("--wait-until", default="", help="HH:MM(KST)까지 대기 후 실행 (예약 실행이 일찍 깬 경우)")
    b.set_defaults(fn=cmd_briefing)
    t = sub.add_parser("notify-test")
    t.add_argument("target", choices=["daily", "wired", "alert"])
    t.set_defaults(fn=cmd_notify_test)
    sub.add_parser("status").set_defaults(fn=cmd_status)
    args = p.parse_args(argv)
    setup_logging(args.verbose)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
