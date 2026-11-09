"""LLM 제공자 추상화. LLM_PROVIDER / LLM_MODEL 환경변수로 고른다.

키는 각 SDK의 기본 환경변수(ANTHROPIC_API_KEY, OPENAI_API_KEY)에서 읽는다.
"""
from __future__ import annotations

import json
import logging
import re
import time
from typing import Protocol

log = logging.getLogger(__name__)

DEFAULT_MODELS = {
    "anthropic": ("claude-haiku-5-5", "claude-sonnet-5-5"),  # (기사 분석용, 브리핑 종합용)
    "openai": ("gpt-5-mini", "gpt-5"),
}


class LLMError(RuntimeError):
    pass


class LLMClient(Protocol):
    name: str

    def complete_json(self, system: str, user: str, *, model: str | None = None, max_tokens: int = 4000) -> dict: ...


def extract_json(text: str) -> dict:
    """응답에서 JSON 객체를 꺼낸다. 코드펜스나 앞뒤 설명이 붙어도 처리."""
    text = text.strip()
    m = re.search(r"```(?:json)?\s*(\{.*\})\s*```", text, re.S)
    if m:
        text = m.group(1)
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end < start:
        raise LLMError("JSON 객체를 찾지 못함")
    try:
        return json.loads(text[start:end + 1])
    except json.JSONDecodeError as e:
        raise LLMError(f"JSON 파싱 실패: {e}") from e


FATAL_ERRORS = {"AuthenticationError", "PermissionDeniedError", "NotFoundError", "BadRequestError"}


class _RetryMixin:
    retries = 3
    disabled_reason = ""

    def _with_retry(self, fn):
        # 인증·권한·모델명 오류는 재시도해도 같다 → 이번 실행 동안 LLM을 끄고 규칙 기반으로 넘긴다
        if self.disabled_reason:
            raise LLMError(f"LLM 비활성화됨: {self.disabled_reason}")
        last = None
        for i in range(1, self.retries + 1):
            try:
                return fn()
            except LLMError:
                raise
            except Exception as e:  # SDK 네트워크/레이트리밋 오류
                if type(e).__name__ in FATAL_ERRORS or "authentication" in str(e).lower():
                    self.disabled_reason = f"{type(e).__name__}: {str(e)[:200]}"
                    log.error("LLM 치명적 오류 → 이번 실행은 규칙 기반으로 진행 (API 키·모델명 확인): %s",
                              self.disabled_reason)
                    raise LLMError(self.disabled_reason) from e
                last = e
                log.warning("LLM 호출 실패(%d/%d): %s", i, self.retries, e)
                time.sleep(2 ** i)
        raise LLMError(f"LLM 호출 재시도 초과: {last}")


class AnthropicClient(_RetryMixin):
    name = "anthropic"

    def __init__(self, model: str, briefing_model: str):
        import anthropic

        self.client = anthropic.Anthropic(max_retries=2)
        self.model, self.briefing_model = model, briefing_model

    def complete_json(self, system, user, *, model=None, max_tokens=4000):
        model = model or self.model
        # 최신 모델은 기본으로 '생각(thinking)'을 켜서 토큰 한도를 먼저 써 버린다 → JSON이 잘린다.
        # 요약·채점은 깊은 추론이 필요 없으므로 effort를 낮추고, Haiku는 thinking을 끈다
        # (Sonnet 5.5·Opus 5.5는 disabled를 거부하므로 effort만 낮춘다).
        extra = {"output_config": {"effort": "low"}}
        if "haiku" in model:
            extra["thinking"] = {"type": "disabled"}

        def call():
            r = self.client.messages.create(model=model, max_tokens=max_tokens, system=system,
                                            messages=[{"role": "user", "content": user}], **extra)
            text = "".join(b.text for b in r.content if getattr(b, "type", "") == "text")
            if r.stop_reason == "max_tokens":
                raise LLMError(f"응답이 토큰 한도({max_tokens})에서 잘림")
            if r.stop_reason == "refusal":
                raise LLMError("모델이 응답을 거부함")
            return text
        return extract_json(self._with_retry(call))


class OpenAIClient(_RetryMixin):
    name = "openai"

    def __init__(self, model: str, briefing_model: str):
        import openai

        self.client = openai.OpenAI(max_retries=2)
        self.model, self.briefing_model = model, briefing_model

    def complete_json(self, system, user, *, model=None, max_tokens=4000):
        def call():
            r = self.client.chat.completions.create(
                model=model or self.model, max_completion_tokens=max_tokens,
                response_format={"type": "json_object"},
                messages=[{"role": "system", "content": system}, {"role": "user", "content": user}])
            return r.choices[0].message.content or ""
        return extract_json(self._with_retry(call))


def get_client(settings) -> LLMClient | None:
    if not settings.llm_enabled:
        return None
    default_model, default_brief = DEFAULT_MODELS[settings.llm_provider]
    model = settings.llm_model or default_model
    brief = settings.llm_briefing_model or default_brief
    try:
        if settings.llm_provider == "anthropic":
            return AnthropicClient(model, brief)
        return OpenAIClient(model, brief)
    except Exception as e:
        log.error("LLM 클라이언트 생성 실패(%s) → 규칙 기반 분석으로 대체: %s", settings.llm_provider, e)
        return None
