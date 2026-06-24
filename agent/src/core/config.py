"""런타임 설정 — 환경변수에서만 읽는다 (Zero-Trust: 비밀은 코드에 두지 않음).

두 가지 LLM 백엔드를 지원하며, 우선순위는 다음과 같다:
1. OpenAI(GPT)  : OPENAI_API + GPT_MODEL 이 있으면 OpenAI 정식 엔드포인트 사용
2. OpenAI 호환  : 위가 없으면 LLM_* (로컬/원격 llama-server, vLLM 등)
"""

from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    """LLM 접속 설정. `from_env()`로만 생성한다."""

    provider: str             # "openai" | "compatible"
    llm_base_url: str | None  # None 이면 OpenAI 기본 엔드포인트(api.openai.com) 사용
    llm_model: str            # 모델 이름
    llm_api_key: str          # OpenAI 키 또는 자체서버 더미("EMPTY")
    temperature: float | None  # None 이면 요청에 temperature 를 보내지 않음(GPT-5 계열 호환)
    request_timeout: float

    @staticmethod
    def _float_or_none(name: str) -> float | None:
        raw = os.environ.get(name, "").strip()
        return float(raw) if raw else None

    @classmethod
    def from_env(cls) -> "Settings":
        timeout = float(os.environ.get("LLM_TIMEOUT", "60"))

        # 1) OpenAI(GPT) 우선
        openai_key = os.environ.get("OPENAI_API", "").strip()
        gpt_model = os.environ.get("GPT_MODEL", "").strip()
        if openai_key and gpt_model:
            return cls(
                provider="openai",
                # OPENAI_BASE_URL 로 override 가능, 없으면 None → 기본 엔드포인트
                llm_base_url=os.environ.get("OPENAI_BASE_URL", "").strip() or None,
                llm_model=gpt_model,
                llm_api_key=openai_key,
                # GPT 계열은 temperature 미지원 모델이 있어 명시할 때만 전송
                temperature=cls._float_or_none("LLM_TEMPERATURE"),
                request_timeout=timeout,
            )

        # 2) OpenAI 호환 서버 (LLM_*)
        model = os.environ.get("LLM_MODEL", "").strip()
        if not model:
            raise ValueError(
                "LLM 설정이 없습니다. OpenAI 사용 시 OPENAI_API + GPT_MODEL, "
                "또는 자체서버 사용 시 LLM_MODEL 을 .env 에 설정하세요 (.env.example 참고)."
            )
        return cls(
            provider="compatible",
            llm_base_url=os.environ.get("LLM_BASE_URL", "http://localhost:8000/v1"),
            llm_model=model,
            llm_api_key=os.environ.get("LLM_API_KEY", "EMPTY"),
            temperature=float(os.environ.get("LLM_TEMPERATURE", "0.0")),
            request_timeout=timeout,
        )