"""Settings.from_env 회귀 (교차검증 발견 수정). agent venv 에서: python -m pytest agent/tests."""

from __future__ import annotations

import pytest

from agent_app.core.config import Settings


def _env(monkeypatch, **kv):
    for k in ("OPENAI_API", "GPT_MODEL", "GPT_MODELS", "LLM_MODEL", "LLM_MODELS",
              "LLM_BASE_URL", "LLM_API_KEY", "LLM_TEMPERATURE", "OPENAI_BASE_URL", "LLM_TIMEOUT",
              "GPT_MAX_TOKENS", "LLM_MAX_TOKENS"):
        monkeypatch.delenv(k, raising=False)
    for k, v in kv.items():
        monkeypatch.setenv(k, v)


def test_empty_temperature_does_not_crash(monkeypatch):
    """LLM_TEMPERATURE='' (키만 남긴 흔한 실수)가 float('') 부팅 크래시를 내지 않아야."""
    _env(monkeypatch, LLM_MODEL="qwen", LLM_TEMPERATURE="")
    s = Settings.from_env()
    assert s.provider == "compatible" and s.temperature == 0.0


@pytest.mark.parametrize("badval", ["", "   "])
def test_empty_timeout_does_not_crash(monkeypatch, badval):
    """LLM_TIMEOUT='' / 공백(키만 남긴 흔한 실수)이 float('') 부팅 크래시를 내지 않아야(temperature 와 대칭)."""
    _env(monkeypatch, LLM_MODEL="qwen", LLM_TIMEOUT=badval)
    s = Settings.from_env()
    assert s.request_timeout == 60.0


def test_valid_timeout_is_honored(monkeypatch):
    _env(monkeypatch, LLM_MODEL="qwen", LLM_TIMEOUT="30")
    assert Settings.from_env().request_timeout == 30.0


def test_openai_key_without_model_is_clear_error(monkeypatch):
    """OPENAI_API 만 주고 GPT_MODEL 없으면 → 자체서버로 silent 무시 대신 명확한 에러."""
    _env(monkeypatch, OPENAI_API="sk-x")              # GPT_MODEL 없음
    with pytest.raises(ValueError, match="GPT_MODEL"):
        Settings.from_env()


def test_openai_key_with_llm_model_does_not_silently_misroute(monkeypatch):
    """OPENAI_API + LLM_MODEL(GPT_MODEL 없음) → OpenAI 키 무시하고 자체서버로 새지 않게 에러."""
    _env(monkeypatch, OPENAI_API="sk-x", LLM_MODEL="qwen")
    with pytest.raises(ValueError, match="GPT_MODEL"):
        Settings.from_env()


def test_openai_full_and_compatible_branches(monkeypatch):
    _env(monkeypatch, OPENAI_API="sk-x", GPT_MODEL="gpt-x")
    assert Settings.from_env().provider == "openai"
    _env(monkeypatch, LLM_MODEL="qwen", LLM_TEMPERATURE="0.7")
    s = Settings.from_env()
    assert s.provider == "compatible" and s.temperature == 0.7


def test_all_from_env_enumerates_multiple_gpt_and_local(monkeypatch):
    """런타임 모델 선택: GPT_MODELS(콤마, 동시 다버전) + 로컬을 전부 열거(GPT 우선·순서보존)."""
    _env(monkeypatch, OPENAI_API="sk-x", GPT_MODELS="gpt-5.4-nano, gpt-5.4-mini", LLM_MODEL="qwen3.6")
    settings = Settings.all_from_env()
    assert [s.llm_model for s in settings] == ["gpt-5.4-nano", "gpt-5.4-mini", "qwen3.6"]
    prov = {s.llm_model: s.provider for s in settings}
    assert prov["gpt-5.4-nano"] == "openai" and prov["qwen3.6"] == "compatible"


def test_all_from_env_singular_gpt_model_backward_compat(monkeypatch):
    _env(monkeypatch, OPENAI_API="sk-x", GPT_MODEL="gpt-5.4-nano")
    assert [s.llm_model for s in Settings.all_from_env()] == ["gpt-5.4-nano"]


def test_all_from_env_dedups_model_ids(monkeypatch):
    _env(monkeypatch, OPENAI_API="sk-x", GPT_MODELS="gpt-a, gpt-a, gpt-b")
    assert [s.llm_model for s in Settings.all_from_env()] == ["gpt-a", "gpt-b"]


def test_all_from_env_requires_some_model(monkeypatch):
    _env(monkeypatch)                                   # 아무 모델도 없음
    with pytest.raises(ValueError):
        Settings.all_from_env()


def test_max_tokens_split_gpt_none_local_default(monkeypatch):
    """G3 max_tokens 는 provider 별로 분리: GPT(openai)=None(OpenAI 모델 한도 위임), 로컬=8192 기본."""
    _env(monkeypatch, OPENAI_API="sk-x", GPT_MODEL="gpt-x")
    assert Settings.from_env().max_tokens is None              # GPT 기본 = None
    _env(monkeypatch, LLM_MODEL="qwen")
    assert Settings.from_env().max_tokens == 8192              # 로컬 기본


def test_max_tokens_env_override_per_provider(monkeypatch):
    """GPT_MAX_TOKENS / LLM_MAX_TOKENS 로 provider 별 명시 override. ≤0=명시적 무제한(None)."""
    _env(monkeypatch, OPENAI_API="sk-x", GPT_MODEL="gpt-x", GPT_MAX_TOKENS="40000")
    assert Settings.from_env().max_tokens == 40000
    _env(monkeypatch, LLM_MODEL="qwen", LLM_MAX_TOKENS="4096")
    assert Settings.from_env().max_tokens == 4096
    _env(monkeypatch, LLM_MODEL="qwen", LLM_MAX_TOKENS="0")    # 0=무제한
    assert Settings.from_env().max_tokens is None


def test_max_tokens_forwarded_to_chatopenai(monkeypatch):
    """build_llm 이 max_tokens 를 ChatOpenAI 에 전달(설정 시)·생략(None)."""
    from agent_app.core.llm import build_llm
    _env(monkeypatch, LLM_MODEL="qwen", LLM_MAX_TOKENS="4096")
    assert build_llm(Settings.from_env()).max_tokens == 4096
    _env(monkeypatch, OPENAI_API="sk-x", GPT_MODEL="gpt-x")    # GPT=None → 미전송
    assert build_llm(Settings.from_env()).max_tokens is None
