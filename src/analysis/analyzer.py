"""기사 1건 분석: LLM 호출 → Pydantic 검증 → 근거 검증 → 점수 합산.

LLM이 없거나 실패하면 규칙 기반 분석으로 대체한다(method='heuristic').
"""
from __future__ import annotations

import json
import logging
from datetime import datetime

from pydantic import ValidationError

from ..config import KST
from ..models import Analysis, LLMAnalysisOut
from .heuristic import heuristic_analyze
from .llm import LLMClient, LLMError
from .validate import ground_check

log = logging.getLogger(__name__)

SYSTEM_PROMPT = """너는 한국 일간지 미디어·테크 담당 데스크를 돕는 뉴스 분석가다.
입력으로 기사 1건의 제목·출처·발행 시각·원문 발췌가 주어진다. 다음 규칙을 반드시 지켜라.

1. 사실(title_ko, summary_ko)은 입력에 있는 내용만 쓴다. 입력에 없는 수치·인명·기관·날짜를 만들지 않는다.
2. 원문 발췌가 짧아 내용을 알 수 없으면 summary_ko에 '원문 발췌가 짧아 세부 내용 미확인'이라고 쓰고 confidence를 low로 한다.
3. why_it_matters, newsroom_impact, reporting_angle은 '분석'이다. 사실처럼 단정하지 말고 '~할 수 있다', '~로 보인다'처럼 쓴다.
4. URL을 출력하지 않는다.
5. 한국어로 쓴다. summary_ko는 2~3문장, 나머지 분석 필드는 각 1~2문장.
6. 출력은 JSON 객체 하나만. 설명이나 코드펜스를 붙이지 않는다.

관련성(relevant): AI 산업·기술 또는 언론·미디어 산업과 실질적으로 관련 있으면 true.
제품 리뷰·쇼핑·생활 기사처럼 관련이 약하면 false.

category: AI(AI 산업·기술) / MEDIA(언론·미디어 산업) / BOTH(AI와 언론·미디어 모두).
  BOTH는 기사가 언론사·방송·뉴스 플랫폼·콘텐츠 산업을 직접 다룰 때만 쓴다(예: 언론사의 AI 도입, AI 학습과 뉴스 저작권,
  AI 검색이 뉴스 트래픽에 미치는 영향). 기업의 AI 발표·투자·인프라 기사는 언론에 '취재 소재'가 될 뿐이므로 AI다.
  newsroom_relevance도 같은 기준이다: 언론사의 업무·수익·권리에 직접 영향이 있을 때만 높게 준다.
subcategory 예시: 모델·제품 출시, 에이전트·자동화, 코딩·개발 도구, 실전 활용법, 투자·M&A, 인프라·GPU,
  정책·규제·저작권, 연구, 뉴스룸 AI 도입, 검색·트래픽, 구독·수익모델, 숏폼·영상·SNS, 미디어 윤리, 기타.
  바이브코딩, 코딩 에이전트(Claude Code·Cursor·Codex·Copilot 등), 프롬프트·워크플로 노하우처럼 독자가 직접
  써 볼 수 있는 실질적인 AI 활용 소식은 '코딩·개발 도구' 또는 '실전 활용법'으로 분류한다.
  '실전 활용법'은 독자가 따라 해 볼 수 있는 도구·기법·사용 후기에만 쓴다. AI로 성공한 개인 사연이나 기업 홍보는 해당하지 않는다.
  이런 기사는 산업 파급력이 작아 보여도 novelty와 usability를 제대로 평가한다(새 도구·새 기법이면 높게).

채점(scores) — 정수로:
- industry_impact 0~30: 산업 전반에 미칠 파급력
- newsroom_relevance 0~30: 언론사·뉴스룸·기자 업무와의 관련성
- novelty 0~25: 새로움과 시의성(새 발표·첫 사례는 높게, 해설·재탕은 낮게)
- usability 0~15: 한국 언론사가 기사·카드뉴스·숏폼으로 바로 활용할 수 있는 정도
90점 이상은 업계 전체가 즉시 알아야 할 중대 사건에만 준다.

JSON 형식:
{"relevant": true, "category": "AI|MEDIA|BOTH", "subcategory": "...", "title_ko": "...", "summary_ko": "...",
 "why_it_matters": "...", "newsroom_impact": "...", "reporting_angle": "...",
 "scores": {"industry_impact": 0, "newsroom_relevance": 0, "novelty": 0, "usability": 0},
 "confidence": "high|medium|low", "confidence_note": "확인 상태를 한 문장으로"}"""


def build_user_prompt(*, title: str, source_name: str, published_at: datetime | None, summary: str,
                      published_uncertain: bool) -> str:
    when = "발행 시각 확인 불가" if (published_at is None or published_uncertain) else \
        published_at.replace(tzinfo=None).isoformat(timespec="minutes") + " UTC"
    payload = {"title": title, "source": source_name, "published": when,
               "excerpt": summary[:1500] or "(발췌 없음)"}
    return "다음 기사를 분석하라.\n" + json.dumps(payload, ensure_ascii=False, indent=1)


def analyze(*, client: LLMClient | None, title: str, summary: str, url: str, source_name: str,
            published_at: datetime | None, published_uncertain: bool, default_category: str,
            group: str, now: datetime) -> Analysis:
    def fallback(reason: str) -> Analysis:
        a = heuristic_analyze(title=title, summary=summary, url=url, published_at=published_at,
                              default_category=default_category, group=group, now=now)
        if reason:
            a.validation_flags.append(reason)
        return a

    if client is None:
        return fallback("")
    user = build_user_prompt(title=title, source_name=source_name, published_at=published_at,
                             summary=summary, published_uncertain=published_uncertain)
    raw = None
    for attempt in (1, 2):  # 형식 오류면 1번 더 요청
        try:
            raw = client.complete_json(SYSTEM_PROMPT, user)
            out = LLMAnalysisOut.model_validate(raw)
            break
        except (LLMError, ValidationError) as e:
            log.warning("LLM 출력 검증 실패(%d/2) %s: %s", attempt, url, str(e)[:300])
            out = None
    if out is None:
        return fallback("llm_failed")

    fields, flags = ground_check(
        {k: getattr(out, k) for k in ("title_ko", "summary_ko", "why_it_matters", "newsroom_impact", "reporting_angle")},
        source_text=f"{title} {summary}", source_url=url)
    confidence = out.confidence
    note = out.confidence_note
    if any(f.startswith("unverified_numbers") for f in flags):
        confidence = "low"
        note = (note + " / 요약에 원문에서 확인되지 않은 수치 포함 — 검증 필요").strip(" /")
    if published_uncertain or published_at is None:
        flags.append("published_at_unknown")
    return Analysis(
        category=out.category, subcategory=out.subcategory, **fields,
        importance_score=out.scores.total, scores=out.scores,
        source_url=url,  # LLM이 준 URL은 쓰지 않는다
        published_at=published_at, confidence=confidence, confidence_note=note,
        relevant=out.relevant, method="llm", model=getattr(client, "model", client.name),
        validation_flags=flags,
    )


def fmt_kst(dt: datetime | None) -> str:
    if dt is None:
        return "발행 시각 확인 불가"
    from datetime import timezone
    return dt.replace(tzinfo=timezone.utc).astimezone(KST).strftime("%m-%d %H:%M KST")
