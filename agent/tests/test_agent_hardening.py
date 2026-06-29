"""agent 코어 견고성 회귀 (교차검증 XV9 발견 수정). agent venv: python -m pytest agent/tests."""

from __future__ import annotations

import os

import pytest

from agent_app.core.agent import ReActAgent
from agent_app.tools.sample import calculator


# ── VULN-1: calculator pow-bomb DoS ──────────────────────────────────────
@pytest.mark.parametrize("expr", ["9**9**9", "10**10**8", "2**100000"])
def test_calculator_blocks_pow_bomb(expr):
    """9**9**9 류 거듭제곱 폭탄이 CPU/메모리를 무한 점유하지 않고 즉시 차단돼야."""
    out = calculator.invoke({"expression": expr})
    assert "너무 큽니다" in out or "오류" in out


@pytest.mark.parametrize("expr,expected", [("2 ** 10", "1024"), ("2 * (3 + 4) ** 2", "98")])
def test_calculator_normal_pow_still_works(expr, expected):
    """정상 범위 거듭제곱은 그대로 계산돼야(가드가 일반 용도를 막지 않음)."""
    assert calculator.invoke({"expression": expr}) == expected


# ── BUG-2: recursion_limit 명시(부분 config 면 langgraph 기본 25 미적용 → 무한루프) ──
def test_config_pins_recursion_limit():
    cfg = ReActAgent._config("t1")
    assert cfg["recursion_limit"] == 25
    assert cfg["configurable"]["thread_id"] == "t1"


# ── VULN-2: resume 가드(interrupt 없는 thread → EmptyInputError 대신 no-op) ──
def test_resume_on_non_interrupt_thread_is_noop():
    os.environ.setdefault("LLM_MODEL", "qwen")
    os.environ.setdefault("LLM_BASE_URL", "http://localhost:8080/v1")
    agent = ReActAgent()  # MemorySaver 기본 — 네트워크 불필요(get_state 만)
    assert list(agent.resume("never-ran-thread-xyz")) == []
