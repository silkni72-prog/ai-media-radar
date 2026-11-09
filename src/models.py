"""모듈 간에 주고받는 데이터 스키마 (Pydantic)."""
from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Literal

from pydantic import BaseModel, Field, field_validator


class Group(str, Enum):
    AI_GLOBAL = "ai_global"
    MEDIA_GLOBAL = "media_global"
    KOREA = "korea"
    KOREA_COMPANY = "korea_company"
    WIRED = "wired"


class SourceConfig(BaseModel):
    id: str
    name: str
    group: Group
    type: Literal["rss", "arxiv_api", "html_list"]
    url: str
    enabled: bool = True
    default_category: Literal["AI", "MEDIA", "BOTH"] = "AI"
    prefilter: bool = False
    max_items: int = 20
    verified: str = ""
    naive_tz: str = ""  # 피드 날짜에 시간대가 없을 때 쓸 시간대 (국내 출처 기본 Asia/Seoul)
    html: dict = Field(default_factory=dict)


class RawArticle(BaseModel):
    """수집기가 돌려주는 원문 정보. 분석은 이 정보 안에서만 이루어진다."""

    source_id: str
    source_name: str
    group: Group
    url: str
    title: str
    summary: str = ""  # RSS description 등 원문 발췌 (HTML 제거)
    author: str = ""
    published_at: datetime | None = None  # UTC (naive)
    published_uncertain: bool = False
    default_category: str = "AI"
    prefilter: bool = False


class ScoreBreakdown(BaseModel):
    industry_impact: int = Field(ge=0, le=30)  # 산업적 파급력
    newsroom_relevance: int = Field(ge=0, le=30)  # 뉴스룸 관련성
    novelty: int = Field(ge=0, le=25)  # 신규성·시의성
    usability: int = Field(ge=0, le=15)  # 취재·콘텐츠 활용도

    @property
    def total(self) -> int:
        return self.industry_impact + self.newsroom_relevance + self.novelty + self.usability


class LLMAnalysisOut(BaseModel):
    """LLM이 돌려줘야 하는 JSON 형식. 범위를 벗어난 점수는 잘라서 받는다."""

    relevant: bool
    category: Literal["AI", "MEDIA", "BOTH"]
    subcategory: str = Field(max_length=40)
    title_ko: str = Field(min_length=1, max_length=200)
    summary_ko: str = Field(min_length=1, max_length=600)
    why_it_matters: str = Field(default="", max_length=400)
    newsroom_impact: str = Field(default="", max_length=400)
    reporting_angle: str = Field(default="", max_length=400)
    scores: ScoreBreakdown
    confidence: Literal["high", "medium", "low"] = "medium"
    confidence_note: str = Field(default="", max_length=200)

    @field_validator("scores", mode="before")
    @classmethod
    def _clamp(cls, v):
        if isinstance(v, dict):
            caps = {"industry_impact": 30, "newsroom_relevance": 30, "novelty": 25, "usability": 15}
            out = {}
            for k, cap in caps.items():
                try:
                    out[k] = max(0, min(cap, int(round(float(v.get(k, 0))))))
                except (TypeError, ValueError):
                    out[k] = 0
            return out
        return v


class Analysis(BaseModel):
    """검증을 마친 최종 분석 결과 (DB 저장 단위)."""

    category: Literal["AI", "MEDIA", "BOTH"]
    subcategory: str
    title_ko: str
    summary_ko: str
    why_it_matters: str = ""
    newsroom_impact: str = ""
    reporting_angle: str = ""
    importance_score: int = Field(ge=0, le=100)
    scores: ScoreBreakdown
    source_url: str
    published_at: datetime | None = None
    confidence: Literal["high", "medium", "low"]
    confidence_note: str = ""
    relevant: bool = True
    method: Literal["llm", "heuristic"] = "llm"
    model: str = ""
    validation_flags: list[str] = Field(default_factory=list)
