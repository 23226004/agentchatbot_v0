"""LLM 클라이언트 빌더 — OpenAI(GPT) 및 OpenAI 호환 서버 공용."""

from __future__ import annotations

from langchain_openai import ChatOpenAI

from src.core.config import Settings


def build_llm(settings: Settings) -> ChatOpenAI:
    """설정으로 ChatOpenAI 클라이언트를 만든다. 이 호출은 서버에 접속하지 않는다."""
    kwargs: dict = {
        "api_key": settings.llm_api_key,
        "model": settings.llm_model,
        "timeout": settings.request_timeout,
    }
    # base_url 이 None 이면 OpenAI 기본 엔드포인트를 쓴다.
    if settings.llm_base_url:
        kwargs["base_url"] = settings.llm_base_url
    # temperature 가 None 이면 요청에 포함하지 않는다 (GPT-5 계열 호환).
    if settings.temperature is not None:
        kwargs["temperature"] = settings.temperature
    return ChatOpenAI(**kwargs)