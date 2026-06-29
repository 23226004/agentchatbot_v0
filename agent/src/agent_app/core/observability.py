"""LLM 관측(Langfuse) — agent 계층에 격리된 **선택적·env-gated** 트레이싱.

시스템 관측(G6 로그/메트릭, backend)과 분리: 여기는 LLM 계층(프롬프트·완성·토큰·비용·ReAct 트레이스).
LangGraph 실행에 Langfuse CallbackHandler 를 끼워 자동 계측한다.

**env-gate**: `LANGFUSE_PUBLIC_KEY`/`SECRET_KEY`/`HOST` **셋 다** 있어야 활성. 없으면 전부 no-op(에이전트
무영향) — langfuse 미설치여도 import 가드로 graceful.
**거버넌스(법령 데이터 — Cloud 금지)**: HOST 를 **필수**로 강제한다. SDK 는 HOST 미설정 시 기본
`https://cloud.langfuse.com` 으로 폴백하므로(키만 설정→법령 프롬프트·검색본문이 Cloud 유출), HOST 누락
또는 Cloud 호스트면 **활성 거부**(self-host 전용). 교차검증 🔴 적발 수정.
v3 SDK: 싱글톤 client 를 host/timeout 명시로 구성, CallbackHandler 는 그 client 사용, session=thread_id.
"""

from __future__ import annotations

import functools
import os
import threading
from typing import Any

# Langfuse Cloud 호스트 — 법령 데이터 유출 방지 위해 활성 거부(self-host 전용).
_CLOUD_HOSTS = ("cloud.langfuse.com",)
# 불통 백엔드가 핫패스를 무한정 잡지 않도록 flush 를 데몬 스레드서 수행하고 이만큼만 join(초).
_FLUSH_JOIN_TIMEOUT = 2.0
# SDK 네트워크 타임아웃(초) — 불통 시 export 가 길게 매달리지 않게.
_SDK_TIMEOUT = 5


def _env(name: str) -> str:
    return (os.environ.get(name) or "").strip()


def host() -> str:
    return _env("LANGFUSE_HOST")


def enabled() -> bool:
    """PUBLIC+SECRET+**HOST** 셋 다(strip 후 비어있지 않음) + HOST 가 Cloud 아님 → 활성.
    HOST 필수·Cloud 거부는 거버넌스(법령 데이터 self-host 전용) 하드 제약이다."""
    if not (_env("LANGFUSE_PUBLIC_KEY") and _env("LANGFUSE_SECRET_KEY") and host()):
        return False
    if any(c in host() for c in _CLOUD_HOSTS):
        return False                                     # Cloud 호스트 → 활성 거부(데이터 유출 방지)
    return True


@functools.lru_cache(maxsize=1)
def _handler() -> Any | None:
    """공유 CallbackHandler(1회 생성). env 없거나 langfuse 미설치면 None.
    ⚠ 캐시는 **프로세스 부팅 시 env 고정** 전제 — 런타임/테스트서 env 토글 시 `reset()` 호출."""
    if not enabled():
        return None
    try:
        from langfuse import Langfuse  # noqa: PLC0415
        from langfuse.langchain import CallbackHandler  # noqa: PLC0415
    except Exception:                                    # noqa: BLE001 — 미설치/임포트 실패 → 비활성
        return None
    try:
        # 싱글톤을 host(self-host) + timeout 명시로 구성 → Cloud 폴백 차단 + 네트워크 타임아웃 단축.
        Langfuse(host=host(), timeout=_SDK_TIMEOUT)
        return CallbackHandler()                         # 위 싱글톤 client 사용
    except Exception:                                    # noqa: BLE001 — 구성 실패해도 에이전트는 정상
        return None


def reset() -> None:
    """핸들러 캐시 무효화. 테스트에서 LANGFUSE_* env 를 토글할 때 staleness(첫 호출 시점 고정) 방지."""
    _handler.cache_clear()


def trace_config(thread_id: str) -> dict[str, Any]:
    """Langfuse 활성 시 langchain config 에 머지할 `callbacks`+`metadata`. 비활성이면 {}.
    thread_id → Langfuse **session**(대화 단위로 턴이 묶임). run 별 trace 는 자동 생성."""
    h = _handler()
    if h is None:
        return {}
    return {"callbacks": [h], "metadata": {"langfuse_session_id": thread_id}}


def flush() -> None:
    """버퍼된 트레이스를 강제 전송. **pump 스레드풀의 단명 컨텍스트**에서 배치 유실을 막으려면 run 종결
    시 호출해야 한다(이 시스템 핵심 주의점). 비활성이면 no-op.

    **핫패스 블로킹 방지(교차검증 🟠 수정)**: Langfuse 불통 시 동기 flush 는 ~3s 블로킹해 매 턴 응답을
    지연시킨다. 데몬 스레드서 flush 하고 짧게 join — 정상(localhost)은 즉시 반환, 불통은 join 캡 후
    반환하고 잔여 전송은 백그라운드(프로세스 생존 중 완료, 데몬이라 종료 막지 않음)."""
    if not enabled():
        return

    def _do() -> None:
        try:
            from langfuse import get_client  # noqa: PLC0415
            get_client().flush()
        except Exception:                     # noqa: BLE001 — flush 실패가 에이전트를 죽이지 않게
            pass

    t = threading.Thread(target=_do, daemon=True, name="langfuse-flush")
    t.start()
    t.join(timeout=_FLUSH_JOIN_TIMEOUT)       # 불통이어도 핫패스를 이 시간으로 제한
