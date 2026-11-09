"""키워드 사전 — LLM 호출 전 1차 필터와 LLM이 없을 때의 규칙 기반 분석에 쓴다."""
from __future__ import annotations

import re

# 대문자 약어는 영문자에 붙어 있지 않을 때만 인정 ('said'의 ai 오탐 방지, 'AI가' 같은 한글 조사 허용)
AI_TERMS = [
    r"(?-i:(?<![A-Za-z])(?:A\.?I|AGI|LLMs?|GPUs?)(?![A-Za-z]))",r"\bGPT[-\w]*", r"\bGemini\b", r"\bClaude\b", r"\bLlama\b",
    r"artificial intelligence", r"machine learning", r"generative", r"chatbot", r"\bagents?\b", r"agentic",
    r"neural", r"foundation model", r"language model", r"OpenAI", r"Anthropic", r"DeepMind", r"Nvidia",
    r"data ?cent(?:er|re)", r"inference", r"transformer", r"diffusion model", r"multimodal",
    "인공지능", "생성형", "챗GPT", "챗봇", "에이전트", "거대언어모델", "언어모델",
    "데이터센터", "반도체", "파운데이션", "딥러닝", "머신러닝", "소버린", "엔비디아", "오픈AI", "앤트로픽", "제미나이",
    # 실전 AI: 바이브코딩·코딩 에이전트·개발 도구
    r"vibe[- ]?cod\w*", r"\bCursor\b", r"Copilot", r"\bCodex\b", r"Claude Code", r"coding agents?", r"\bMCP\b",
    r"prompt engineering", r"system prompts?", r"\bRAG\b", r"\bfine-?tun\w*", r"open[- ]weights?", r"\bOllama\b", r"Windsurf", r"Replit", r"\bLovable\b",
    "바이브 ?코딩", "코딩 에이전트", "코파일럿", "커서", "클로드 코드", "프롬프트", "노코드", "자동화",
]

MEDIA_TERMS = [
    r"journalis\w*", r"newsroom", r"publisher", r"\bnews\b", r"\bmedia\b", r"editor", r"reporter",
    r"subscription", r"paywall", r"audience", r"\btraffic\b", r"search", r"\bSEO\b", r"copyright",
    r"licens\w+", r"misinformation", r"disinformation", r"fact-check", r"broadcast", r"podcast",
    r"short-form", r"TikTok", r"YouTube", r"newsletter", r"press",
    "언론", "기자", "뉴스룸", "저널리즘", "신문", "방송", "편집", "보도", "구독", "유료화", "숏폼", "유튜브",
    "포털", "네이버 뉴스", "검색", "알고리즘", "저작권", "트래픽", "카드뉴스", "미디어", "팩트체크", "허위정보",
    "언론사", "방송사", "종편", "보도국", "앵커", "시청률", "공영방송", "방미통위", "방통위", "OTT", "넷플릭스",
    r"(?-i:JTBC|KBS|MBC|SBS|YTN|EBS|MBN|TV조선|채널A|연합뉴스|뉴시스|뉴스1)",
    "조선일보", "중앙일보", "동아일보", "한겨레", "경향신문", "한국일보", "세계일보", "국민일보", "매일경제", "한국경제",
]

IMPACT_TERMS = [
    r"launch", r"releases?", r"unveil", r"announce", r"acqui", r"funding", r"raises?", r"\$\d", r"billion",
    r"regulat", r"\blaw\b", r"lawsuit", r"court", r"ban", r"policy", r"partnership", r"deal",
    "출시", "공개", "발표", "인수", "투자", "유치", "규제", "법안", "소송", "판결", "협약", "제휴", "조원", "억원",
]

_AI_RX = re.compile("|".join(AI_TERMS), re.I)
_MEDIA_RX = re.compile("|".join(MEDIA_TERMS), re.I)
_IMPACT_RX = re.compile("|".join(IMPACT_TERMS), re.I)


def count(rx: re.Pattern, text: str) -> int:
    return len(rx.findall(text or ""))


def ai_hits(text: str) -> int:
    return count(_AI_RX, text)


def media_hits(text: str) -> int:
    return count(_MEDIA_RX, text)


def impact_hits(text: str) -> int:
    return count(_IMPACT_RX, text)


NOISE_RX = re.compile(r"^\s*\[(인사|부고|부음|포토|알림|사고|게시판|오늘의 운세|신간)\]")


# WIRED 등 해외 매체의 쇼핑·리뷰·할인 글 (예: "8 Best Computer Speakers (2026)", "Black Friday Deals")
SHOPPING_RX = re.compile(
    r"^\s*(the\s+)?\d+\s+best\b|\bbest\b.*\(20\d\d\)|\b(best|early|top)\b.*\bdeals?\b|\bdeals?:|\d+% off|\bon sale\b|\bsale:|\bblack friday\b|\bcyber monday\b"
    r"|\bgift guide\b|\bprime day\b|\bcoupon|\bpromo code|\breview:\s|\bsave \$\d", re.I)


def is_noise(title: str) -> bool:
    """인사·부고·포토 같은 단신과 쇼핑·할인 글은 분석 대상에서 뺀다."""
    t = title or ""
    return bool(NOISE_RX.match(t) or SHOPPING_RX.search(t))


def prefilter(article) -> bool:
    """기사량이 많은 피드에서 AI·언론과 무관한 기사를 LLM 호출 전에 걸러낸다.
    제목에 키워드가 있거나, 요약에 AI 키워드가 2번 이상 나오면 통과."""
    title = article.title or ""
    body = article.summary or ""
    if is_noise(title):
        return False
    if ai_hits(title) or (media_hits(title) and ai_hits(body)):
        return True
    if ai_hits(body) >= 2:
        return True
    # 언론 전문지(미디어오늘·기자협회보)는 미디어 키워드만으로도 통과
    if article.default_category in ("MEDIA", "BOTH") and media_hits(title):
        return True
    return False
