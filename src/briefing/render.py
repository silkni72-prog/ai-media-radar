"""브리핑 Markdown/HTML 렌더링과 저장, 푸시 본문 생성."""
from __future__ import annotations

import html
import re
from pathlib import Path

from ..analysis.analyzer import fmt_kst
from ..analysis.clustering import Issue
from ..notifications.ntfy import Push
from datetime import datetime

from .builder import Briefing, is_practical

CONF_KO = {"high": "높음", "medium": "보통", "low": "낮음"}


def _refs(b: Briefing, refs: list[int]) -> str:
    out = []
    for r in refs:
        it = b.ref_issue(r)
        if it:
            out.append(f"[{it.rep.source_name}]({it.rep.url})")
    return " · ".join(out)


SECTIONS = [
    ("part_ai", "① AI 최신 동향", "모델·제품·투자부터 바이브코딩·코딩 에이전트 같은 실전 활용까지"),
    ("part_media", "② 언론 동향", "언론사·플랫폼·저작권 등 뉴스 산업 변화 (AI 관련 우선)"),
    ("part_wired", "③ WIRED 주요 이슈", "WIRED AI·Business·Backchannel·Top Stories"),
]
WEEKDAY = "월화수목금토일"


def _item_md(i: int, it: Issue, *, media: bool) -> list[str]:
    a, row = it.rep.analysis, it.rep
    tag = "🛠 " if is_practical(it) else ""
    meta = [f"**{a.importance_score}점**", row.source_name, fmt_kst(row.published_at)]
    if row.published_uncertain:
        meta.append("⚠️발행 시각 미확인")
    if it.related:
        meta.append(f"관련 보도 {len(it.related)}건")
    lines = [f"### {i}. {tag}{a.title_ko}", " · ".join(meta), "", a.summary_ko]
    if row.group not in ("korea", "korea_company") and row.title != a.title_ko:
        lines.append(f"- **원제**: {row.title}")
    point = (a.newsroom_impact or a.why_it_matters) if media else (a.why_it_matters or a.newsroom_impact)
    if point:
        lines.append(f"- **왜 중요한가** _(분석)_: {point}")
    flags = [f for f in a.validation_flags if f.startswith("unverified_numbers")]
    if flags or (a.confidence == "low" and a.method == "llm"):
        lines.append(f"- ⚠️ **확인 필요**: {a.confidence_note or '원문 대조 필요'}")
    links = [f"[원문 보기]({row.url})"] + [f"[{r.source_name}]({r.url})" for r in it.related[:3]]
    lines.append("- " + " · ".join(links))
    return lines + [""]


def to_markdown(b: Briefing) -> str:
    d = datetime.strptime(b.date_kst, "%Y-%m-%d")
    md = [f"# AI × MEDIA 모닝 브리핑 | {b.date_kst} ({WEEKDAY[d.weekday()]})", "",
          f"_집계 {fmt_kst(b.window_start)} ~ {fmt_kst(b.window_end)} · 기사 {b.stats.get('articles', 0)}건 분석_", ""]
    if b.method != "llm":
        md += ["> ⚠️ 일부 또는 전부를 LLM 없이 키워드 규칙으로 선별했습니다. 제목·요약은 원문 발췌이며 점수는 참고용입니다.", ""]

    md += ["## 오늘의 핵심 3줄", ""]
    md += [f"- {h.text} — {_refs(b, h.refs)}" for h in b.headlines] or ["- 오늘은 기준을 넘는 주요 기사가 없습니다."]
    md.append("")

    # 한눈에 보기: 섹션별 제목만 (휴대폰에서 훑어보기용)
    md += ["## 한눈에 보기", ""]
    for attr, title, _ in SECTIONS:
        items = getattr(b, attr)
        md.append(f"**{title}** ({len(items)}건)")
        md += [f"{n}. {'🛠 ' if is_practical(it) else ''}{it.rep.analysis.title_ko} ({it.score})"
               for n, it in enumerate(items, 1)] or ["_해당 없음_"]
        md.append("")
    if b.angles:
        md += [f"**④ 오늘의 취재각** ({len(b.angles)}건)"] + [f"{n}. {ag.title}" for n, ag in enumerate(b.angles, 1)] + [""]

    for attr, title, desc in SECTIONS:
        items = getattr(b, attr)
        hours = b.stats.get("hours", {}).get(attr, 24)
        note = f" · 기사가 적어 최근 {hours}시간으로 넓힘(주말·휴일 포함)" if hours > 24 else ""
        md += [f"## {title}", "", f"_{desc}{note}_", ""]
        if not items:
            md += ["_지난 24시간 동안 기준을 넘는 기사가 없습니다._", ""]
        for n, it in enumerate(items, 1):
            md += _item_md(n, it, media=(attr == "part_media"))
    md += ["## ④ 오늘의 취재각", "", "_국내 언론사가 바로 기획해 볼 만한 주제 (위 기사들을 근거로 시스템이 제안)_", ""]
    if b.angles:
        for n, ag in enumerate(b.angles, 1):
            md += [f"### {n}. {ag.title}", "", ag.pitch, "", f"- 근거: {_refs(b, ag.refs)}", ""]
    else:
        md += ["_근거가 충분한 취재각이 없어 생략합니다._", ""]
    md += ["---", "_🛠 = 바이브코딩·코딩 도구 등 실전 활용 소식. 요약은 원문 기반 사실, '(분석)'은 시스템의 해석입니다. "
           "인용 전 원문을 확인하세요._", ""]
    return "\n".join(md)


def _inline(text: str) -> str:
    t = html.escape(text)
    t = re.sub(r"\[([^\]]+)\]\((https?://[^)]+)\)", r'<a href="\2">\1</a>', t)
    t = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", t)
    t = re.sub(r"(?<![\w\"=>])_(.+?)_(?!\w)", r"<em>\1</em>", t)
    t = re.sub(r'(?<!href=")(?<!">)(https?://[^\s<]+)', r'<a href="\1">\1</a>', t)
    return t


def to_html(md: str, title: str) -> str:
    """의존성 없이 이 브리핑 형식만 처리하는 간단한 Markdown → HTML."""
    out, in_list = [], False
    for line in md.splitlines():
        if line.startswith("- "):
            if not in_list:
                out.append("<ul>")
                in_list = True
            out.append(f"<li>{_inline(line[2:])}</li>")
            continue
        if in_list:
            out.append("</ul>")
            in_list = False
        if re.match(r"^\d+\. ", line):
            out.append(f"<p class='li'>{_inline(line)}</p>")
        elif line.startswith("### "):
            out.append(f"<h3>{_inline(line[4:])}</h3>")
        elif line.startswith("## "):
            out.append(f"<h2>{_inline(line[3:])}</h2>")
        elif line.startswith("# "):
            out.append(f"<h1>{_inline(line[2:])}</h1>")
        elif line.startswith("> "):
            out.append(f"<blockquote>{_inline(line[2:])}</blockquote>")
        elif line.strip() == "---":
            out.append("<hr>")
        elif line.strip():
            out.append(f"<p>{_inline(line.rstrip(' '))}</p>")
    if in_list:
        out.append("</ul>")
    css = """:root{--bg:#fff;--fg:#1a1a1a;--muted:#666;--acc:#0b57d0;--line:#e5e5e5;--note:#fff7e0}
@media (prefers-color-scheme:dark){:root{--bg:#121212;--fg:#e8e8e8;--muted:#9a9a9a;--acc:#8ab4f8;--line:#2c2c2c;--note:#2a2410}}
body{background:var(--bg);color:var(--fg);font:16px/1.65 -apple-system,'Apple SD Gothic Neo','Malgun Gothic',sans-serif;max-width:760px;margin:0 auto;padding:16px}
h1{font-size:1.5rem}h2{font-size:1.2rem;border-top:2px solid var(--fg);padding-top:12px;margin-top:32px}
h3{font-size:1.05rem;margin:20px 0 6px}a{color:var(--acc);word-break:break-all}
li{margin:3px 0}blockquote{background:var(--note);margin:0;padding:8px 12px;border-radius:6px}
hr{border:0;border-top:1px solid var(--line)}em{color:var(--muted)}p.li{margin:2px 0 2px 8px}"""
    return (f"<!doctype html><html lang='ko'><head><meta charset='utf-8'>"
            f"<meta name='viewport' content='width=device-width,initial-scale=1'><title>{html.escape(title)}</title>"
            f"<style>{css}</style></head><body>{''.join(out)}</body></html>")


def save(b: Briefing, output_dir: Path) -> tuple[Path, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    md = to_markdown(b)
    md_path = output_dir / f"briefing-{b.date_kst}.md"
    html_path = output_dir / f"briefing-{b.date_kst}.html"
    md_path.write_text(md, encoding="utf-8")
    html_path.write_text(to_html(md, f"AI × MEDIA 모닝 브리핑 {b.date_kst}"), encoding="utf-8")
    (output_dir / "latest.md").write_text(md, encoding="utf-8")
    (output_dir / "index.html").write_text(to_html(md, f"AI × MEDIA 모닝 브리핑 {b.date_kst}"), encoding="utf-8")
    return md_path, html_path


def daily_push(b: Briefing, topic: str, public_base_url: str = "") -> Push:
    lines = [f"• {h.text}" for h in b.headlines] or ["오늘은 기준을 넘는 주요 기사가 없습니다."]
    for attr, title, _ in SECTIONS:
        items = getattr(b, attr)[:2]
        if items:
            lines += ["", f"{title} ({len(getattr(b, attr))}건)"] + [f"- {it.rep.analysis.title_ko[:50]}" for it in items]
    click = f"{public_base_url}/briefing-{b.date_kst}.html" if public_base_url else ""
    actions = [{"action": "view", "label": "전체 브리핑", "url": click}] if click else []
    return Push(topic=topic, title=f"AI×MEDIA 모닝 브리핑 | {b.date_kst}", message="\n".join(lines),
                priority=3, tags=["newspaper"], click=click, actions=actions)
