import re
from datetime import datetime, timedelta

import yaml

from src.analysis.analyzer import analyze
from src.briefing import render
from src.briefing.builder import build_briefing
from src.config import KST, ROOT
from src.main import wait_until_kst
from src.models import Group, RawArticle
from tests.conftest import FakeLLM, llm_json, utcnow


def seed(repo, n_ai=7, n_media=2, client=None):
    now = utcnow()
    items = []
    for i in range(n_ai):
        items.append(RawArticle(source_id="tc", source_name="TechCrunch AI", group=Group.AI_GLOBAL,
                                url=f"https://tc.com/{i}", title=f"Model launch number{i} unique{i}",
                                summary="A model launch.", published_at=now - timedelta(hours=3 + i)))
    for i in range(n_media):
        items.append(RawArticle(source_id="nl", source_name="Nieman Lab", group=Group.MEDIA_GLOBAL,
                                url=f"https://nl.org/{i}", title=f"Newsroom tool story{i} distinct{i}",
                                summary="Newsrooms adopt AI.", published_at=now - timedelta(hours=2)))
    items.append(RawArticle(source_id="old", source_name="Old", group=Group.AI_GLOBAL, url="https://old/1",
                            title="Old news", published_at=now - timedelta(hours=30)))
    rows = repo.add_articles(items)
    for idx, r in enumerate(rows):
        is_media = r.source_id == "nl"
        resp = llm_json(category="MEDIA" if is_media else "AI", title_ko=f"제목{idx}",
                        subcategory="뉴스룸 AI 도입" if is_media else ["모델·제품 출시", "연구", "인프라·GPU", "투자·M&A"][idx % 4],
                        score=(20, 25 if is_media else 5, 20, 10 - idx % 5))
        a = analyze(client=FakeLLM(resp), title=r.title, summary=r.summary, url=r.url, source_name=r.source_name,
                    published_at=r.published_at, published_uncertain=False, default_category="AI",
                    group=r.group, now=utcnow())
        repo.save_analysis(r, a, issue_key=r.url_hash)
    repo.s.commit()


def test_briefing_selection_and_render(repo, tmp_path):
    seed(repo)
    synth = {"headlines": [{"text": "첫째 변화가 있었다", "refs": [1]}, {"text": "근거 없는 문장", "refs": [99]}],
             "angles": [{"title": "국내 뉴스룸 AI 도입", "pitch": "주요 언론사의 도입 현황을 확인한다. https://bad.example",
                         "refs": [8]}, {"title": "근거 없는 기획", "pitch": "근거 번호가 범위를 벗어난 취재각이다.", "refs": [99]}]}
    b = build_briefing(repo, FakeLLM(synth))
    assert len(b.part_ai) == 7  # 기준을 넘는 7건 모두 (최대 10)
    scores = [i.score for i in b.part_ai]
    assert scores == sorted(scores, reverse=True)  # 중요도 순
    subs = [i.rep.analysis.subcategory for i in b.part_ai]
    assert max(subs.count(s) for s in set(subs)) <= 3  # 같은 주제 과다 방지
    assert len(b.part_media) == 2  # 기사가 2건뿐이면 없는 기사를 만들지 않음
    assert all("old" not in i.rep.url for i in b.part_ai)  # 24시간 밖 제외
    assert [h.text for h in b.headlines] == ["첫째 변화가 있었다"]  # 잘못된 근거 번호는 버림
    assert [a.title for a in b.angles] == ["국내 뉴스룸 AI 도입"] and "bad.example" not in b.angles[0].pitch
    md = render.to_markdown(b)
    for sec in ["AI × MEDIA 모닝 브리핑", "오늘의 핵심 3줄", "한눈에 보기", "① AI 최신 동향", "② 언론 동향",
                "③ WIRED 주요 이슈", "④ 오늘의 취재각", "원제", "왜 중요한가", "원문 보기"]:
        assert sec in md
    md_path, html_path = render.save(b, tmp_path)
    assert md_path.exists() and "<h2>" in html_path.read_text(encoding="utf-8")
    p = render.daily_push(b, "t", "https://pages.example/radar")
    assert p.title == f"AI×MEDIA 모닝 브리핑 | {b.date_kst}"
    assert p.click == f"https://pages.example/radar/briefing-{b.date_kst}.html"
    assert len(p.payload()["message"]) <= 900


def _add(repo, *, src, group, n, score, subcategory="모델·제품 출시", category="AI", relevant=True):
    now = utcnow()
    rows = repo.add_articles([RawArticle(source_id=src, source_name=src, group=group, url=f"https://{src}.com/{i}",
                                         title=f"{src} story{i} token{i} unique{i} words{i}", summary="s",
                                         published_at=now - timedelta(hours=1 + i)) for i in range(n)])
    subs = subcategory if isinstance(subcategory, list) else [subcategory]
    for k, r in enumerate(rows):
        resp = llm_json(category=category, title_ko=f"{src} 제목", subcategory=subs[k % len(subs)], score=score)
        resp["relevant"] = relevant
        a = analyze(client=FakeLLM(resp), title=r.title, summary=r.summary, url=r.url, source_name=r.source_name,
                    published_at=r.published_at, published_uncertain=False, default_category="AI",
                    group=r.group, now=utcnow())
        repo.save_analysis(r, a, issue_key=r.url_hash)
    repo.s.commit()


def test_sections_fill_to_five_cap_at_ten_and_keep_practical(repo):
    _add(repo, src="big", group=Group.AI_GLOBAL, n=12, score=(25, 5, 20, 10),
         subcategory=["모델·제품 출시", "연구", "인프라·GPU", "투자·M&A"])  # 60점 12건
    _add(repo, src="vibe", group=Group.AI_GLOBAL, n=1, score=(5, 5, 15, 10), subcategory="코딩·개발 도구")  # 35점
    _add(repo, src="nl", group=Group.MEDIA_GLOBAL, n=2, score=(10, 25, 10, 5), category="MEDIA")  # 50점
    _add(repo, src="nlow", group=Group.MEDIA_GLOBAL, n=5, score=(5, 15, 5, 5), category="MEDIA")  # 30점
    _add(repo, src="junk", group=Group.MEDIA_GLOBAL, n=3, score=(2, 5, 5, 2), category="MEDIA")  # 14점
    _add(repo, src="wired", group=Group.WIRED, n=3, score=(10, 2, 15, 5), relevant=False)  # 32점, AI 무관
    b = build_briefing(repo, None)
    assert len(b.part_ai) == 10  # 최대 10개
    assert any(i.rep.source_id == "vibe" for i in b.part_ai)  # 점수가 낮아도 실전 활용 기사 자리 보장
    assert len(b.part_media) == 5  # 기준 넘는 2건 + 차점 3건으로 최소 5개
    assert all(i.rep.source_id != "junk" for i in b.part_media)  # 25점 미만으로는 채우지 않음
    assert len(b.part_wired) == 3  # WIRED는 AI 관련성과 무관하게 주요 기사 표시 (15점 이상)
    assert 1 <= len(b.angles) <= 3  # LLM이 없어도 기사별 취재 관점으로 취재각 제안
    assert all(i.rep.group != "wired" for i in b.part_ai + b.part_media)


def test_one_topic_day_still_shows_five(repo):
    _add(repo, src="same", group=Group.AI_GLOBAL, n=8, score=(25, 5, 20, 10))  # 전부 같은 세부 주제
    assert len(build_briefing(repo, None).part_ai) == 5


def test_empty_day_does_not_force_items(repo):
    b = build_briefing(repo, None)
    assert b.part_ai == [] and b.part_media == [] and b.part_wired == []
    assert "기준을 넘는 기사가 없습니다" in render.to_markdown(b)


def test_wait_until_kst():
    slept = []
    now = datetime(2026, 11, 9, 8, 45, tzinfo=KST)
    assert wait_until_kst("09:00", now_fn=lambda: now, sleep=slept.append) == 900
    assert wait_until_kst("09:00", now_fn=lambda: now.replace(hour=9, minute=5), sleep=slept.append) == 0
    assert wait_until_kst("09:00", now_fn=lambda: now.replace(hour=6), sleep=slept.append) == 0  # 너무 이르면 대기 안 함
    assert slept == [900]


def test_workflow_cron_matches_kst_schedule():
    wf = yaml.safe_load((ROOT / ".github" / "workflows" / "radar.yml").read_text(encoding="utf-8"))
    crons = [c["cron"] for c in wf[True]["schedule"]]  # PyYAML은 'on'을 True로 읽는다
    assert "40 23 * * *" in crons and "10 0 * * *" in crons
    # 23:40 UTC = 08:40 KST, 00:10 UTC = 09:10 KST
    to_kst = lambda h, m: ((h + 9) % 24, m)
    assert to_kst(23, 40) == (8, 40) and to_kst(0, 10) == (9, 10)
    wired = [c for c in crons if re.match(r"^\S+ \* \* \* \*$", c) and "," in c][0]
    assert len(wired.split()[0].split(",")) == 2  # 30분 간격
