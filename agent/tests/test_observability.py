"""observability(Langfuse 연결) 회귀 고정 — env-gate·거버넌스(Cloud 거부)·lru staleness·flush 안전.

교차검증(Do-30-XV) 적발 결함을 테스트로 못박는다:
- 🔴 HOST 미설정 시 SDK 가 cloud.langfuse.com 폴백 → 법령데이터 유출 → enabled() 가 HOST 강제·Cloud 거부.
- 🟠 _handler lru_cache staleness → reset() 로 무효화.
- 🟡 whitespace 키 → strip.
"""

from __future__ import annotations

import pytest

from agent_app.core import observability as obs

_PUB = "LANGFUSE_PUBLIC_KEY"
_SEC = "LANGFUSE_SECRET_KEY"
_HOST = "LANGFUSE_HOST"
_SELF = "http://localhost:3001"


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    """각 테스트 전후로 LANGFUSE_* 제거 + 핸들러 캐시 무효화(staleness 격리)."""
    for k in (_PUB, _SEC, _HOST):
        monkeypatch.delenv(k, raising=False)
    obs.reset()
    yield
    obs.reset()


def _set(monkeypatch, pub="pk-lf-x", sec="sk-lf-x", host=_SELF):
    if pub is not None:
        monkeypatch.setenv(_PUB, pub)
    if sec is not None:
        monkeypatch.setenv(_SEC, sec)
    if host is not None:
        monkeypatch.setenv(_HOST, host)


# ── env-gate: off / 부분키 / whitespace ──────────────────────────────────────
def test_disabled_by_default():
    assert obs.enabled() is False
    assert obs.trace_config("t1") == {}
    assert obs.flush() is None            # no-op, 무예외


def test_partial_keys_disabled(monkeypatch):
    _set(monkeypatch, sec=None)           # PUBLIC+HOST 만
    assert obs.enabled() is False
    _set(monkeypatch, pub=None, sec="sk-lf-x")  # SECRET+HOST 만(PUBLIC 제거)
    monkeypatch.delenv(_PUB, raising=False)
    assert obs.enabled() is False


def test_whitespace_keys_disabled(monkeypatch):
    _set(monkeypatch, pub="  ", sec="  ", host="  ")
    assert obs.enabled() is False         # strip 후 빈문자 → 비활성(가짜키 활성 방지)


# ── 거버넌스: HOST 필수 + Cloud 거부 (🔴) ────────────────────────────────────
def test_host_required_no_cloud_fallback(monkeypatch):
    """키만 있고 HOST 없으면 비활성 — SDK 의 cloud.langfuse.com 폴백(법령데이터 유출) 차단."""
    _set(monkeypatch, host=None)
    monkeypatch.delenv(_HOST, raising=False)
    assert obs.enabled() is False
    assert obs.trace_config("t1") == {}


def test_cloud_host_rejected(monkeypatch):
    """HOST 가 Cloud 면 활성 거부(거버넌스 self-host 전용)."""
    _set(monkeypatch, host="https://cloud.langfuse.com")
    assert obs.enabled() is False
    _set(monkeypatch, host="https://us.cloud.langfuse.com")
    assert obs.enabled() is False


def test_selfhost_enabled(monkeypatch):
    _set(monkeypatch, host=_SELF)
    assert obs.enabled() is True


# ── trace_config 모양(활성) ──────────────────────────────────────────────────
def test_trace_config_shape_when_enabled(monkeypatch):
    _set(monkeypatch)
    cfg = obs.trace_config("thread-abc")
    assert "callbacks" in cfg and len(cfg["callbacks"]) == 1
    assert cfg["metadata"]["langfuse_session_id"] == "thread-abc"


# ── lru staleness: reset() 로 무효화 (🟠) ────────────────────────────────────
def test_handler_cache_staleness_needs_reset(monkeypatch):
    """env off 로 먼저 호출→None 캐시. on 해도 reset 전엔 stale. reset 후 활성."""
    assert obs.trace_config("t") == {}    # None 캐시됨
    _set(monkeypatch)                     # 이제 env on
    assert obs.enabled() is True
    assert obs.trace_config("t") == {}    # 그러나 stale 캐시 → 여전히 {}
    obs.reset()
    assert "callbacks" in obs.trace_config("t")  # 무효화 후 활성


# ── flush 안전(활성·self-host) ───────────────────────────────────────────────
def test_flush_enabled_no_raise(monkeypatch):
    _set(monkeypatch)
    obs.trace_config("t")                 # 핸들러 구성
    obs.flush()                           # localhost(미가동이어도) 데몬+join 가드라 무예외·바운드
