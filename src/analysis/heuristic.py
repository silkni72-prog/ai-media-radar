"""LLM 키가 없거나 호출이 실패했을 때 쓰는 규칙 기반 분석.

번역·요약을 지어내지 않는다. 원문 제목과 원문 발췌를 그대로 쓰고 method='heuristic'으로 표시한다.
"""
from __future__ import annotations

import re
from datetime import datetime

from ..models import Analysis, ScoreBreakdown
from . import keywords as kw

SUBCATS = [
    ("모델·제품 출시", r"launch|release|unveil|introduc|model|출시|공개|모델"),
    ("에이전트·자동화", r"agent|automat|에이전트|자동화"),
    ("투자·M&A", r"acqui|funding|raise|invest|valuation|IPO|인수|투자|상장"),
    ("인프라·GPU", r"GPU|chip|data ?cent|infrastructure|반도체|데이터센터|칩"),
    ("정책·규제·저작권", r"regulat|law|policy|copyright|lawsuit|court|규제|법|저작권|소송|정책"),
    ("연구", r"research|paper|benchmark|arxiv|study|연구|논문"),
    ("뉴스룸 AI 도입", r"newsroom|journalis.*AI|AI.*journalis|뉴스룸|언론.*AI|AI.*언론|기자.*AI"),
    ("검색·트래픽", r"search|traffic|referral|SEO|검색|트래픽|포털"),
    ("구독·수익모델", r"subscri|paywall|revenue|advertis|구독|유료|광고|수익"),
    ("숏폼·영상·SNS", r"video|short|TikTok|YouTube|social|podcast|영상|숏폼|유튜브|SNS"),
]


def _subcategory(text: str, category: str) -> str:
    for name, rx in SUBCATS:
        if re.search(rx, text, re.I):
            return name
    return "미디어 일반" if category == "MEDIA" else "AI 일반"


def _first_sentences(text: str, n: int = 2, limit: int = 280) -> str:
    parts = re.split(r"(?<=[.!?。])\s+|(?<=다\.)\s*", text or "")
    out = " ".join(p for p in parts[:n] if p).strip()
    return (out[:limit] + "…") if len(out) > limit else out


def heuristic_analyze(*, title: str, summary: str, url: str, published_at: datetime | None,
                      default_category: str, group: str, now: datetime) -> Analysis:
    text = f"{title} {summary}"
    ai, media, impact = kw.ai_hits(text), kw.media_hits(text), kw.impact_hits(text)
    media_source = default_category in ("MEDIA", "BOTH")
    if ai and (media >= 3 or (media_source and media >= 1)):
        category = "BOTH"
    elif media and (media_source or media > ai):
        category = "MEDIA"
    elif ai:
        category = "AI"
    else:
        category = default_category
    relevant = bool(ai or media >= 2 or group in ("ai_global", "media_global"))

    industry = min(30, 8 + 5 * impact + (4 if group == "ai_global" else 0))
    newsroom = min(30, 4 + 5 * media + (8 if category in ("MEDIA", "BOTH") else 0))
    if published_at is None:
        novelty = 10
    else:
        age_h = max(0.0, (now - published_at).total_seconds() / 3600)
        novelty = 25 if age_h < 6 else 20 if age_h < 12 else 15 if age_h < 24 else 8
    usability = min(15, 5 + 2 * (ai > 0) + 3 * (media > 0) + 2 * (impact > 0))
    scores = ScoreBreakdown(industry_impact=industry, newsroom_relevance=newsroom, novelty=novelty, usability=usability)

    excerpt = _first_sentences(summary) or "(원문 요약 없음)"
    return Analysis(
        category=category, subcategory=_subcategory(text, category),
        title_ko=title,  # 번역하지 않음 — LLM 미사용 표시
        summary_ko=f"[원문 발췌] {excerpt}",
        why_it_matters="", newsroom_impact="", reporting_angle="",
        importance_score=scores.total, scores=scores, source_url=url, published_at=published_at,
        confidence="low", confidence_note="LLM 미사용: 키워드 규칙으로 분류·채점, 번역·요약 없음",
        relevant=relevant, method="heuristic", model="rules-v1",
    )
