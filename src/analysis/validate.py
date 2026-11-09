"""LLM 출력의 근거 검증. 원문에 없는 URL·수치가 끼어들지 못하게 한다."""
from __future__ import annotations

import re

URL_RX = re.compile(r"https?://\S+|www\.\S+", re.I)
# 2자리 이상 숫자(쉼표·소수점 포함). '2~3문장' 같은 1자리 숫자는 무시
NUM_RX = re.compile(r"\d[\d,]*\.?\d+")


def _norm_num(s: str) -> str:
    return s.replace(",", "").rstrip(".")


def numbers_in(text: str) -> set[str]:
    return {_norm_num(n) for n in NUM_RX.findall(text or "")}


def strip_foreign_urls(text: str, allowed: set[str]) -> tuple[str, bool]:
    removed = False

    def repl(m):
        nonlocal removed
        u = m.group(0).rstrip(").,]")
        if u in allowed:
            return m.group(0)
        removed = True
        return ""

    return re.sub(r"\s{2,}", " ", URL_RX.sub(repl, text or "")).strip(), removed


def ground_check(fields: dict[str, str], source_text: str, source_url: str) -> tuple[dict[str, str], list[str]]:
    """텍스트 필드에서 원문에 없는 URL을 지우고, 원문에 없는 수치를 플래그로 남긴다."""
    flags: list[str] = []
    allowed = {source_url}
    src_nums = numbers_in(source_text)
    cleaned: dict[str, str] = {}
    for name, value in fields.items():
        v, removed = strip_foreign_urls(value, allowed)
        if removed:
            flags.append(f"url_removed:{name}")
        cleaned[name] = v
    # 요약(사실 영역)에 원문에 없는 숫자가 있으면 검증 필요로 표시
    unknown = sorted(n for n in numbers_in(cleaned.get("summary_ko", "")) if n not in src_nums)
    if unknown:
        flags.append("unverified_numbers:" + ",".join(unknown[:5]))
    return cleaned, flags
