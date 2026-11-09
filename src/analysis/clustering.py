"""같은 사건을 다룬 기사 묶기(이슈 클러스터)와 주제 다양성 선별."""
from __future__ import annotations

from dataclasses import dataclass, field

from ..collectors.normalize import jaccard, title_tokens

SIM_THRESHOLD = 0.4
MIN_SHARED = 4


def tokens_for(title: str, title_ko: str = "") -> set[str]:
    return title_tokens(title) | title_tokens(title_ko)


def similarity(a: set[str], b: set[str]) -> float:
    """자카드 유사도와 겹침 계수 중 큰 값. 제목 길이가 크게 다른 경우를 위해 겹침 계수도 보되,
    공유 토큰이 MIN_SHARED개 미만이면 우연한 일치로 보고 자카드만 쓴다."""
    shared = len(a & b)
    if not shared:
        return 0.0
    j = jaccard(a, b)
    if shared < MIN_SHARED:
        return j
    return max(j, shared / min(len(a), len(b)))


def assign_issue_key(tokens: set[str], candidates: list[tuple[str, set[str]]], fallback_key: str,
                     threshold: float = SIM_THRESHOLD) -> str:
    """가장 비슷한 기존 이슈가 기준 이상이면 그 키를, 아니면 새 키를 돌려준다."""
    best_key, best = None, 0.0
    for key, toks in candidates:
        s = similarity(tokens, toks)
        if s > best:
            best_key, best = key, s
    return best_key if (best_key and best >= threshold) else fallback_key


@dataclass
class Issue:
    key: str
    rep: object  # ArticleRow (대표 기사)
    related: list = field(default_factory=list)  # 같은 이슈의 다른 기사

    @property
    def score(self) -> int:
        return self.rep.analysis.importance_score

    @property
    def sources(self) -> list[str]:
        names = [self.rep.source_name] + [r.source_name for r in self.related]
        return list(dict.fromkeys(names))


def group_issues(rows: list) -> list[Issue]:
    """issue_key로 묶고 점수가 가장 높은 기사를 대표로. 점수 내림차순."""
    buckets: dict[str, list] = {}
    for r in rows:
        buckets.setdefault(r.analysis.issue_key or r.url_hash, []).append(r)
    issues = []
    for key, items in buckets.items():
        items.sort(key=lambda r: (r.analysis.importance_score, r.analysis.method == "llm"), reverse=True)
        issues.append(Issue(key=key, rep=items[0], related=items[1:]))
    issues.sort(key=lambda i: i.score, reverse=True)
    return issues


def pick_diverse(issues: list[Issue], n: int, *, min_score: int, per_subcategory: int = 2,
                 exclude: set[str] | None = None) -> list[Issue]:
    """점수순으로 고르되 같은 세부 주제는 per_subcategory개까지만. 기준 미달이면 억지로 채우지 않는다."""
    exclude = exclude or set()
    picked, counts = [], {}
    for it in issues:
        if it.key in exclude or it.score < min_score:
            continue
        sub = it.rep.analysis.subcategory
        if counts.get(sub, 0) >= per_subcategory:
            continue
        picked.append(it)
        counts[sub] = counts.get(sub, 0) + 1
        if len(picked) >= n:
            break
    return picked
