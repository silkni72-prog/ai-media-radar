import json
from datetime import timedelta

import httpx

from src import pipeline
from src.models import Group, RawArticle
from src.notifications.ntfy import NtfyNotifier, Push
from tests.conftest import FakeLLM, llm_json, utcnow


def transport(statuses, sink):
    seq = iter(statuses)

    def handler(req: httpx.Request):
        sink.append(json.loads(req.content))
        st = next(seq)
        if st == "neterr":
            raise httpx.ConnectError("boom")
        return httpx.Response(st, json={"id": "abc"})
    return httpx.Client(transport=httpx.MockTransport(handler))


def push():
    return Push(topic="t-daily", title="제목 한글", message="x" * 5000, tags=["newspaper"], click="https://c")


def test_ntfy_retries_then_succeeds_and_records(settings, repo):
    sent = []
    n = NtfyNotifier(settings, repo, client=transport(["neterr", 503, 200], sent), sleep=lambda s: None)
    r = n.send("daily", "2026-11-09", push())
    assert r.status == "sent" and r.attempts == 3 and r.message_id == "abc"
    body = sent[-1]
    assert body["topic"] == "t-daily" and body["title"] == "제목 한글" and body["click"] == "https://c"
    assert len(body["message"]) <= 900  # 푸시 본문은 짧게
    # 같은 키는 다시 보내지 않는다
    assert n.send("daily", "2026-11-09", push()).status == "duplicate"
    assert len(sent) == 3


def test_ntfy_4xx_not_retried_and_failure_can_retry_later(settings, repo):
    sent = []
    n = NtfyNotifier(settings, repo, client=transport([403, 200], sent), sleep=lambda s: None)
    r = n.send("daily", "d1", push())
    assert r.status == "failed" and r.attempts == 1
    assert n.send("daily", "d1", push()).status == "sent"  # 실패 이력은 재시도 가능


def test_ntfy_dry_run_and_missing_topic(settings, repo):
    settings.ntfy_dry_run = True
    sent = []
    n = NtfyNotifier(settings, repo, client=transport([200], sent))
    assert n.send("daily", "d2", push()).status == "dry_run" and sent == []
    p = push()
    p.topic = ""
    assert n.send("daily", "d3", p).status == "no_topic"


def _wired_items(now):
    def it(i, title, published=True):
        return RawArticle(source_id="wired_ai", source_name="WIRED — AI", group=Group.WIRED,
                          url=f"https://www.wired.com/story/s{i}/", title=title, summary=f"AI story {i} about newsrooms",
                          published_at=(now - timedelta(hours=1)) if published else None,
                          published_uncertain=not published, default_category="AI")
    return [it(1, "OpenAI signs deal with news publishers"), it(2, "Minor AI gadget review"),
            it(3, "AI search kills publisher traffic", published=False)]


def test_wired_monitor_alerts_once(settings, repo, monkeypatch):
    now = utcnow()
    monkeypatch.setattr(pipeline, "load_sources", lambda p: [
        type("S", (), {"id": "wired_ai", "group": Group.WIRED, "type": "rss"})()])
    monkeypatch.setattr(pipeline, "sources_by_group", lambda s, wired: s)
    monkeypatch.setattr(pipeline, "collect_source", lambda s, st, known_urls=None: _wired_items(now))

    def respond(system, user):
        if "Minor" in user:
            return llm_json(score=(10, 5, 10, 5), title_ko="작은 기사")
        if "traffic" in user:
            return llm_json(score=(25, 28, 20, 12), title_ko="AI 검색과 트래픽")  # 85점
        return llm_json(score=(29, 29, 22, 13), title_ko="오픈AI 언론사 계약")  # 93점
    sent = []
    notifier = NtfyNotifier(settings, repo, client=transport([200] * 10, sent), sleep=lambda s: None)
    rep = pipeline.run_wired(repo, settings, FakeLLM(respond), notifier)
    assert rep["analyzed"] == 3
    assert rep["alerts"] == {"wired": 2, "breaking": 1}  # 80점 이상 2건, 90점 이상 1건
    titles = [b["title"] for b in sent]
    assert "[WIRED] 오픈AI 언론사 계약" in titles and "[속보] 오픈AI 언론사 계약" in titles
    uncertain = [b for b in sent if "트래픽" in b["title"]][0]
    assert "발행 시각" in uncertain["message"]  # 발행 시각 미확인 표시
    assert all(b["click"].startswith("https://www.wired.com/") for b in sent)

    # 재실행: 같은 기사는 새로 저장되지 않고 알림도 다시 가지 않는다
    rep2 = pipeline.run_wired(repo, settings, FakeLLM(respond), notifier)
    assert rep2["new"] == 0 and rep2["alerts"] == {"wired": 0, "breaking": 0}
    assert len(sent) == 3


def test_heuristic_scores_never_push(settings, repo, monkeypatch):
    now = utcnow()
    monkeypatch.setattr(pipeline, "load_sources", lambda p: [
        type("S", (), {"id": "wired_ai", "group": Group.WIRED, "type": "rss"})()])
    monkeypatch.setattr(pipeline, "sources_by_group", lambda s, wired: s)
    monkeypatch.setattr(pipeline, "collect_source", lambda s, st, known_urls=None: _wired_items(now))
    sent = []
    notifier = NtfyNotifier(settings, repo, client=transport([200] * 10, sent))
    rep = pipeline.run_wired(repo, settings, None, notifier)
    assert rep["analyzed"] == 3 and sent == []


def test_collect_failure_is_logged_and_others_continue(settings, repo, monkeypatch):
    S = lambda i: type("S", (), {"id": i, "group": Group.AI_GLOBAL, "type": "rss"})()
    monkeypatch.setattr(pipeline, "load_sources", lambda p: [S("bad"), S("good")])
    monkeypatch.setattr(pipeline, "sources_by_group", lambda s, wired: s)

    def fake(s, st, known_urls=None):
        if s.id == "bad":
            raise httpx.ConnectError("down")
        return [RawArticle(source_id="good", source_name="g", group=Group.AI_GLOBAL, url="https://g/1", title="AI model")]
    monkeypatch.setattr(pipeline, "collect_source", fake)
    rep = pipeline.collect(repo, settings, wired=False)
    assert rep["failed"][0][0] == "bad" and rep["new"] == 1
    assert repo.recent_fetch_failures()[0].source_id == "bad"


def test_round_robin_spreads_llm_quota_across_sources():
    from types import SimpleNamespace
    from src.pipeline import _round_robin
    rows = [SimpleNamespace(source_id="zdnet", n=i) for i in range(10)] + \
           [SimpleNamespace(source_id="mediatoday", n=0), SimpleNamespace(source_id="techcrunch", n=0)]
    picked = _round_robin(rows, 4)
    assert [r.source_id for r in picked] == ["zdnet", "mediatoday", "techcrunch", "zdnet"]
    assert [r.n for r in picked if r.source_id == "zdnet"] == [0, 1]  # 출처 안에서는 최신순 유지
