from datetime import datetime

from src.analysis.analyzer import analyze
from src.analysis.clustering import assign_issue_key, tokens_for
from src.analysis.llm import LLMError, extract_json
from src.analysis.validate import ground_check
from tests.conftest import FakeLLM, llm_json

NOW = datetime(2026, 11, 9, 0, 0)
BASE = dict(title="Publisher signs AI licensing deal worth $20 million", url="https://ex.com/a",
            summary="The publisher signed a $20 million deal with an AI company in 2026.",
            source_name="Ex", published_at=datetime(2026, 11, 8, 22, 0), published_uncertain=False,
            default_category="MEDIA", group="media_global", now=NOW)


def test_extract_json_handles_fences_and_noise():
    assert extract_json('설명\n```json\n{"a": 1}\n```') == {"a": 1}
    assert extract_json('앞말 {"a": {"b": 2}} 뒷말') == {"a": {"b": 2}}
    try:
        extract_json("no json")
        assert False
    except LLMError:
        pass


def test_llm_analysis_valid_and_score_summed():
    a = analyze(client=FakeLLM(llm_json(score=(28, 29, 20, 12), summary="2026년 20 million 달러 계약을 맺었다.")), **BASE)
    assert a.method == "llm" and a.importance_score == 89
    assert a.source_url == "https://ex.com/a" and a.confidence == "high"
    assert not a.validation_flags


def test_scores_are_clamped_to_rubric():
    a = analyze(client=FakeLLM(llm_json(score=(99, 99, 99, 99))), **BASE)
    assert a.scores.industry_impact == 30 and a.scores.usability == 15 and a.importance_score == 100


def test_hallucinated_url_removed_and_unknown_numbers_flagged():
    out = llm_json(summary="계약 규모는 3500만 달러다. 자세한 내용은 https://evil.example/x 참고.",
                   why_it_matters="https://other.example 에서 보듯 중요하다.", source_url="https://evil.example/x")
    a = analyze(client=FakeLLM(out), **BASE)
    assert "evil.example" not in a.summary_ko and "other.example" not in a.why_it_matters
    assert a.source_url == BASE["url"]
    assert any(f.startswith("unverified_numbers:3500") for f in a.validation_flags)
    assert a.confidence == "low"


def test_invalid_llm_output_retries_then_falls_back():
    bad = {"category": "NEWS"}  # 스키마 위반
    llm = FakeLLM([bad, bad])
    a = analyze(client=llm, **BASE)
    assert llm.calls == 2 and a.method == "heuristic" and "llm_failed" in a.validation_flags
    # 두 번째 시도에서 고쳐지면 LLM 결과 사용
    a2 = analyze(client=FakeLLM([bad, llm_json()]), **BASE)
    assert a2.method == "llm"


def test_heuristic_never_invents_translation():
    a = analyze(client=None, **BASE)
    assert a.title_ko == BASE["title"] and a.summary_ko.startswith("[원문 발췌]")
    assert a.confidence == "low" and a.method == "heuristic"
    assert 0 <= a.importance_score <= 100


def test_unknown_publish_time_flagged():
    a = analyze(client=FakeLLM(llm_json()), **{**BASE, "published_at": None, "published_uncertain": True})
    assert "published_at_unknown" in a.validation_flags


def test_ground_check_keeps_numbers_present_in_source():
    _, flags = ground_check({"summary_ko": "1,500명이 참여했다."}, "1500 people joined", "https://x")
    assert flags == []


def test_issue_clustering():
    cands = [("k1", tokens_for("OpenAI launches GPT-6 model", "오픈AI GPT-6 모델 출시"))]
    assert assign_issue_key(tokens_for("OpenAI launches GPT-6", "오픈AI, GPT-6 모델 출시"), cands, "new") == "k1"
    assert assign_issue_key(tokens_for("Nvidia earnings beat", "엔비디아 실적"), cands, "new") == "new"
    # 국내 매체 간 띄어쓰기·말머리 차이
    kr = [("jtbc", tokens_for("[속보] JTBC, 매각 공고 게시… “내년 1월 중 우선협상대상자 선정할 것”"))]
    assert assign_issue_key(tokens_for("JTBC 매각공고 게시… 새 주인 찾기 본격화"), kr, "new") == "jtbc"
    assert assign_issue_key(tokens_for("JTBC 새 예능 시청률 상승"), kr, "new") == "new"
