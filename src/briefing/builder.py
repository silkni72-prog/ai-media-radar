"""아침 브리핑 구성: ① AI 최신 동향 ② 언론 동향(AI 중심) ③ WIRED 주요 이슈.

각 섹션은 중요도 순으로 5~10개. 기준(45점)을 넘는 이슈가 5개보다 적으면 그다음 점수 이슈로 5개까지
채우되, 점수가 너무 낮은 기사(25점 미만)로는 채우지 않는다.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from pydantic import BaseModel, Field, ValidationError

from ..analysis.clustering import Issue, group_issues, pick_diverse
from ..analysis.llm import LLMClient, LLMError
from ..analysis.validate import strip_foreign_urls
from ..config import KST
from ..models import Group
from ..storage.repository import Repository

log = logging.getLogger(__name__)

SECTION_MIN = 5          # 섹션당 최소 개수(기사가 있으면)
SECTION_MAX = 10         # 섹션당 최대 개수
MAIN_MIN_SCORE = 45      # '주요 이슈' 기준
FILL_MIN_SCORE = 25      # 최소 개수를 채울 때도 이 점수 미만은 넣지 않는다
PRACTICAL_SUBS = {"코딩·개발 도구", "실전 활용법"}
PRACTICAL_QUOTA = 4      # AI 섹션 10개 중 바이브코딩·코딩 도구·실전 활용 기사 자리 보장(있으면)
WIRED_FILL_MIN = 15      # WIRED는 AI 무관 기사도 싣되, 제품 리뷰·생활 팁 같은 15점 미만 글로는 채우지 않는다
WINDOW_STEPS = (24, 48, 72)  # 섹션이 5개 미만이면 집계 구간을 이 순서로 넓힌다


class Headline(BaseModel):
    text: str = Field(min_length=5, max_length=200)
    refs: list[int] = Field(min_length=1)


class Angle(BaseModel):
    title: str = Field(min_length=3, max_length=120)
    pitch: str = Field(min_length=10, max_length=500)
    refs: list[int] = Field(min_length=1)


class SynthesisOut(BaseModel):
    headlines: list[Headline] = Field(max_length=3)
    angles: list[Angle] = Field(default_factory=list, max_length=3)


@dataclass
class Briefing:
    date_kst: str
    window_start: datetime
    window_end: datetime
    headlines: list[Headline]
    part_ai: list[Issue]
    part_media: list[Issue]
    part_wired: list[Issue]
    method: str  # llm | heuristic | mixed
    stats: dict = field(default_factory=dict)
    angles: list[Angle] = field(default_factory=list)

    @property
    def all_items(self) -> list[Issue]:
        return self.part_ai + self.part_media + self.part_wired

    def ref_issue(self, ref: int) -> Issue | None:
        allx = self.all_items
        return allx[ref - 1] if 1 <= ref <= len(allx) else None


SYNTH_SYSTEM = """너는 한국 일간지 디지털뉴스부 데스크다. 오늘 아침 브리핑에 실린 기사 목록(번호·섹션·제목·요약)이 주어진다.
1) headlines: 오늘 가장 중요한 변화 3가지를 각 1문장으로 쓴다. 가능하면 AI 동향·언론 동향·WIRED에서 고르게 고른다.
2) angles: 국내 언론사가 취재할 만한 '취재각' 1~3개. 국내 언론사의 AI 전환, 뉴스룸 자동화, 카드뉴스·숏폼 제작,
   뉴스 유통·검색 트래픽 변화, 기자가 바로 써 볼 수 있는 AI 도구와 연결되는 것을 우선한다.
   title은 짧은 기획 제목, pitch는 무엇을 누구에게 확인할지 2~3문장. 근거가 약하면 개수를 줄인다.
목록에 있는 내용만 근거로 쓰고 refs에 근거 기사 번호를 넣는다. URL은 쓰지 않는다.
JSON 하나만 출력: {"headlines":[{"text":"...","refs":[1]}],"angles":[{"title":"...","pitch":"...","refs":[2]}]}"""


def is_practical(it: Issue) -> bool:
    return it.rep.analysis.subcategory in PRACTICAL_SUBS


def _is_media(it: Issue) -> bool:
    a = it.rep.analysis
    return a.category == "MEDIA" or (a.category == "BOTH" and (a.scores or {}).get("newsroom_relevance", 0) >= 20)


def pick_section(issues: list[Issue], *, practical_quota: int = 0, fill_min: int = FILL_MIN_SCORE) -> list[Issue]:
    """중요도 순 5~10개. 같은 세부 주제는 3개까지만(주제 다양성)."""
    picked = pick_diverse(issues, SECTION_MAX, min_score=MAIN_MIN_SCORE, per_subcategory=3)
    if len(picked) < SECTION_MIN:
        more = pick_diverse(issues, SECTION_MIN - len(picked), min_score=fill_min, per_subcategory=3,
                            exclude={i.key for i in picked})
        picked += more
    if len(picked) < SECTION_MIN:  # 주제가 한쪽에 몰린 날: 다양성 제한을 풀고 5개까지
        picked += pick_diverse(issues, SECTION_MIN - len(picked), min_score=fill_min, per_subcategory=SECTION_MAX,
                               exclude={i.key for i in picked})
    if practical_quota:
        have = sum(is_practical(i) for i in picked)
        keys = {i.key for i in picked}
        for cand in [i for i in issues if is_practical(i) and i.key not in keys and i.score >= fill_min]:
            if have >= practical_quota:
                break
            if len(picked) >= SECTION_MAX:  # 자리가 없으면 가장 낮은 일반 기사와 교체
                others = [i for i in picked if not is_practical(i)]
                if not others:
                    break
                picked.remove(min(others, key=lambda i: i.score))
            picked.append(cand)
            have += 1
    return sorted(picked, key=lambda i: i.score, reverse=True)


def _synthesize(client: LLMClient, b: Briefing) -> tuple[list[Headline], list[Angle]] | None:
    allx = b.all_items
    sec = {id(i): s for s, part in (("AI 동향", b.part_ai), ("언론 동향", b.part_media), ("WIRED", b.part_wired))
           for i in part}
    lines = []
    for n, it in enumerate(allx, 1):
        a = it.rep.analysis
        lines.append(f"[{n}] ({sec[id(it)]}, {a.importance_score}점) {a.title_ko}\n요약: {a.summary_ko}\n"
                     f"중요한 이유: {a.why_it_matters}")
    try:
        raw = client.complete_json(SYNTH_SYSTEM, "\n\n".join(lines), model=getattr(client, "briefing_model", None),
                                   max_tokens=8000)
        out = SynthesisOut.model_validate(raw)
    except (LLMError, ValidationError) as e:
        log.warning("핵심 3줄·취재각 생성 실패 → 규칙 기반 대체: %s", str(e)[:300])
        return None
    n = len(allx)
    hs = [h for h in out.headlines if all(1 <= r <= n for r in h.refs)]  # 근거 번호가 틀린 문장은 버린다
    ags = [a for a in out.angles if all(1 <= r <= n for r in a.refs)]
    for h in hs:
        h.text = strip_foreign_urls(h.text, set())[0]
    for a in ags:
        a.pitch = strip_foreign_urls(a.pitch, set())[0]
    return hs, ags


def _heuristic_angles(b: Briefing) -> list[Angle]:
    """LLM이 없으면 기사별 '취재 관점'에서 뉴스룸 관련성이 높은 순으로 3개."""
    allx = b.all_items
    ranked = sorted(allx, key=lambda i: (i.rep.analysis.scores or {}).get("newsroom_relevance", 0), reverse=True)
    out = []
    for it in ranked:
        a = it.rep.analysis
        if a.reporting_angle and len(a.reporting_angle) >= 10 and len(out) < 3:
            out.append(Angle(title=(a.title_ko or it.rep.title)[:120], pitch=a.reporting_angle[:500],
                             refs=[allx.index(it) + 1]))
    return out


def _heuristic_headlines(b: Briefing) -> list[Headline]:
    allx = b.all_items
    hs = []
    for part in (b.part_ai, b.part_media, b.part_wired):
        if part:
            it = part[0]
            t = it.rep.analysis.title_ko if len(it.rep.analysis.title_ko) >= 5 else it.rep.title
            hs.append(Headline(text=t[:200], refs=[allx.index(it) + 1]))
    return hs


def build_briefing(repo: Repository, client: LLMClient | None, *, now_utc: datetime | None = None) -> Briefing:
    now_utc = now_utc or datetime.now(timezone.utc).replace(tzinfo=None)
    start = now_utc - timedelta(hours=24)
    non_wired = [g.value for g in Group if g != Group.WIRED]
    seen_rows: dict[int, object] = {}

    def section(filter_fn, *, groups, relevant_only=True, **kw) -> tuple[list[Issue], int]:
        """24시간 안에서 5개가 안 되면(주말·휴일) 48→72시간으로 넓힌다. 넓힌 시간을 함께 돌려준다."""
        picked, hours = [], 24
        for hours in WINDOW_STEPS:
            rows = repo.analyzed_in_window(now_utc - timedelta(hours=hours), now_utc, groups=groups,
                                           relevant_only=relevant_only)
            seen_rows.update({r.id: r for r in rows})
            picked = pick_section([i for i in group_issues(rows) if filter_fn(i)], **kw)
            if len(picked) >= SECTION_MIN:
                break
        return picked, hours

    part_ai, h_ai = section(lambda i: not _is_media(i), groups=non_wired, practical_quota=PRACTICAL_QUOTA)
    part_media, h_media = section(_is_media, groups=non_wired)
    # WIRED는 AI·언론 관련성과 무관하게 '주요 이슈'를 본다 (점수 순)
    part_wired, h_wired = section(lambda i: True, groups=[Group.WIRED.value], relevant_only=False, fill_min=WIRED_FILL_MIN)

    date_kst = now_utc.replace(tzinfo=timezone.utc).astimezone(KST).strftime("%Y-%m-%d")
    all_rows = list(seen_rows.values())
    llm_count = sum(1 for r in all_rows if r.analysis.method == "llm")
    b = Briefing(date_kst=date_kst, window_start=start, window_end=now_utc, headlines=[], part_ai=part_ai,
                 part_media=part_media, part_wired=part_wired,
                 method="llm" if (client and llm_count) else "heuristic",
                 stats={"articles": len(all_rows), "llm_analyzed": llm_count,
                        "hours": {"part_ai": h_ai, "part_media": h_media, "part_wired": h_wired}})
    synth = _synthesize(client, b) if (client and b.all_items) else None
    if synth is None:
        synth = (_heuristic_headlines(b), _heuristic_angles(b))
        b.method = "heuristic" if not llm_count else "mixed"
    b.headlines, b.angles = synth
    return b
