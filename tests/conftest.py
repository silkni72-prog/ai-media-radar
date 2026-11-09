from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from src.config import Settings
from src.storage.db import make_session_factory
from src.storage.repository import Repository


def utcnow():
    return datetime.now(timezone.utc).replace(tzinfo=None)


@pytest.fixture
def settings(tmp_path):
    st = Settings()
    st.database_url = f"sqlite:///{(tmp_path / 'test.db').as_posix()}"
    st.output_dir = tmp_path / "output"
    st.ntfy_dry_run = False
    st.ntfy_daily_topic, st.ntfy_wired_topic, st.ntfy_alert_topic = "t-daily", "t-wired", "t-alert"
    st.ntfy_base_url = "https://ntfy.example"
    st.llm_provider = "none"
    return st


@pytest.fixture
def repo(settings):
    s = make_session_factory(settings.database_url)()
    yield Repository(s)
    s.close()


class FakeLLM:
    """정해진 JSON을 돌려주는 가짜 LLM. responses가 함수면 (system, user)로 호출."""

    name = "fake"
    model = "fake-model"
    briefing_model = "fake-brief"

    def __init__(self, responses):
        self.responses = responses
        self.calls = 0

    def complete_json(self, system, user, *, model=None, max_tokens=1200):
        self.calls += 1
        r = self.responses
        if callable(r):
            return r(system, user)
        if isinstance(r, list):
            item = r[min(self.calls - 1, len(r) - 1)]
            if isinstance(item, Exception):
                raise item
            return item
        return r


def llm_json(score=(25, 25, 20, 10), relevant=True, category="BOTH", summary="요약 문장이다. 두 번째 문장이다.",
             title_ko="한국어 제목", subcategory="뉴스룸 AI 도입", **extra):
    d = {"relevant": relevant, "category": category, "subcategory": subcategory, "title_ko": title_ko,
         "summary_ko": summary, "why_it_matters": "중요할 수 있다.", "newsroom_impact": "뉴스룸에 영향이 있을 수 있다.",
         "reporting_angle": "국내 언론사 도입 현황을 취재할 수 있다.",
         "scores": dict(zip(["industry_impact", "newsroom_relevance", "novelty", "usability"], score)),
         "confidence": "high", "confidence_note": "원문 발췌 기반"}
    d.update(extra)
    return d


@pytest.fixture
def recent():
    return utcnow() - timedelta(hours=2)
