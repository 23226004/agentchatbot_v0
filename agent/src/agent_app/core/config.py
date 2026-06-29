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
    max_tokens: int | None = None  # LLM 응답 토큰 상한(러너웨이 출력 DoS 바운드, G3). None=무제한.

    @staticmethod
    def _float_or_none(name: str) -> float | None:
        raw = os.environ.get(name, "").strip()
        return float(raw) if raw else None

    @staticmethod
    def _max_tokens(env_name: str, default: int | None) -> int | None:
        """LLM 응답 토큰 상한(content_md/summary/tool.call args 를 근원에서 바운드, G3). **provider 별로
        분리** 적용한다:
          · **GPT(openai)**: 기본 None — OpenAI 가 모델별 출력 상한을 이미 강제하므로 앱 캡 없이도 바운드
            (절대 절단 안 함). 명시 캡 원하면 `GPT_MAX_TOKENS`.
          · **로컬 호환서버(qwen 등)**: 기본 8192 — self-host 라 외부 강제가 없어 앱 캡이 1차 바운드.
            16k 컨텍스트 thinking 모델에 여유. 더 필요하면 `LLM_MAX_TOKENS`.
        env 미설정=provider 기본. ≤0=명시적 무제한. 비정수는 부팅 시 ValueError(fail-loud)."""
        raw = os.environ.get(env_name, "").strip()
        if not raw:
            return default
        n = int(raw)
        return n if n > 0 else None

    @classmethod
    def from_env(cls) -> "Settings":
        # strip 가드: LLM_TIMEOUT='' (키만 남긴 흔한 실수)가 float('') 부팅 크래시 안 내게(temperature 와 대칭).
        _to = os.environ.get("LLM_TIMEOUT", "").strip()
        timeout = float(_to) if _to else 60.0
        gpt_max_tokens = cls._max_tokens("GPT_MAX_TOKENS", None)    # GPT — OpenAI 모델 한도에 위임(앱 캡 없음)
        llm_max_tokens = cls._max_tokens("LLM_MAX_TOKENS", 8192)    # 로컬 호환서버 — qwen 16k thinking 1차 캡

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
                max_tokens=gpt_max_tokens,
            )

        # OpenAI 키만 주고 GPT_MODEL 누락 → 자체서버로 **조용히 새지** 않게 명확히 안내(키 무시 함정 방지).
        if openai_key and not gpt_model:
            raise ValueError(
                "OPENAI_API 는 설정됐지만 GPT_MODEL 이 없습니다. OpenAI 사용 시 GPT_MODEL 을 설정하거나, "
                "자체서버(LLM_*) 사용 시 OPENAI_API 를 제거하세요."
            )

        # 2) OpenAI 호환 서버 (LLM_*)
        model = os.environ.get("LLM_MODEL", "").strip()
        if not model:
            raise ValueError(
                "LLM 설정이 없습니다. OpenAI 사용 시 OPENAI_API + GPT_MODEL, "
                "또는 자체서버 사용 시 LLM_MODEL 을 .env 에 설정하세요 (.env.example 참고)."
            )
        # 빈 문자열(`LLM_TEMPERATURE=`)로 끄려는 흔한 실수가 float('') 부팅 크래시를 내지 않게 strip+기본.
        _temp = os.environ.get("LLM_TEMPERATURE", "").strip()
        return cls(
            provider="compatible",
            llm_base_url=os.environ.get("LLM_BASE_URL", "http://localhost:8000/v1"),
            llm_model=model,
            llm_api_key=os.environ.get("LLM_API_KEY", "EMPTY"),
            temperature=float(_temp) if _temp else 0.0,
            request_timeout=timeout,
            max_tokens=llm_max_tokens,
        )

    @staticmethod
    def _split(name: str) -> list[str]:
        """콤마구분 env → 모델 id 리스트(공백 제거, 빈값 제외)."""
        return [m.strip() for m in os.environ.get(name, "").split(",") if m.strip()]

    @classmethod
    def all_from_env(cls) -> list["Settings"]:
        """**구성된 모든** LLM 백엔드를 열거(런타임 모델 선택용) — GPT 여러 버전 + 로컬 호환서버들.

        from_env 가 우선순위로 **하나**만 고르는 것과 달리, 설정된 모델을 **전부** 반환한다(레지스트리).
        - GPT: `GPT_MODELS`(콤마구분, 동시 다버전) 또는 `GPT_MODEL`(단수, 하위호환). 같은 OPENAI_API/base 공유.
        - 로컬: `LLM_MODELS`(콤마구분) 또는 `LLM_MODEL`(단수). 같은 LLM_BASE_URL 공유.
        첫 원소 = from_env 와 같은 우선순위 기본값(GPT → 로컬). 모델 id 중복은 첫 정의 유지. 없으면 ValueError.
        """
        _to = os.environ.get("LLM_TIMEOUT", "").strip()
        timeout = float(_to) if _to else 60.0
        gpt_max_tokens = cls._max_tokens("GPT_MAX_TOKENS", None)    # GPT — OpenAI 모델 한도에 위임(앱 캡 없음)
        llm_max_tokens = cls._max_tokens("LLM_MAX_TOKENS", 8192)    # 로컬 호환서버 — qwen 16k thinking 1차 캡
        out: list[Settings] = []
        seen: set[str] = set()

        def _add(s: "Settings") -> None:
            if s.llm_model not in seen:          # 모델 id = 레지스트리 키(중복 방지)
                seen.add(s.llm_model)
                out.append(s)

        # GPT — 여러 버전 동시(GPT_MODELS) 또는 단수(GPT_MODEL). 키 1개, 모델만 다름.
        openai_key = os.environ.get("OPENAI_API", "").strip()
        if openai_key:
            base = os.environ.get("OPENAI_BASE_URL", "").strip() or None
            temp = cls._float_or_none("LLM_TEMPERATURE")
            for gm in (cls._split("GPT_MODELS") or cls._split("GPT_MODEL")):
                _add(cls(provider="openai", llm_base_url=base, llm_model=gm,
                         llm_api_key=openai_key, temperature=temp, request_timeout=timeout,
                         max_tokens=gpt_max_tokens))

        # 로컬 호환서버 — LLM_MODELS(콤마) 또는 LLM_MODEL(단수).
        _temp = os.environ.get("LLM_TEMPERATURE", "").strip()
        local_base = os.environ.get("LLM_BASE_URL", "http://localhost:8000/v1")
        local_key = os.environ.get("LLM_API_KEY", "EMPTY")
        for lm in (cls._split("LLM_MODELS") or cls._split("LLM_MODEL")):
            _add(cls(provider="compatible", llm_base_url=local_base, llm_model=lm,
                     llm_api_key=local_key, temperature=float(_temp) if _temp else 0.0,
                     request_timeout=timeout, max_tokens=llm_max_tokens))

        if not out:
            raise ValueError(
                "LLM 설정이 없습니다. OpenAI(GPT): OPENAI_API + GPT_MODELS(또는 GPT_MODEL), "
                "자체서버: LLM_MODELS(또는 LLM_MODEL). 여러 개 설정하면 런타임에 모델 선택 가능."
            )
        return out