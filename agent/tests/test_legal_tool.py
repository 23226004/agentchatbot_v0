"""search_legal 입력 가드 회귀 (교차검증 XV10). agent venv: python -m pytest agent/tests."""

from __future__ import annotations

import pytest

from agent_app.tools.legal import make_legal_tool


def _call(tool, query):
    """content_and_artifact 도구는 ToolCall 로 호출해야 ToolMessage(content+artifact) 반환."""
    msg = tool.invoke({"type": "tool_call", "name": tool.name,
                       "args": {"query": query}, "id": "t1"})
    return msg.content, msg.artifact


class _SpyService:
    """retrieve 가 호출됐는지 추적하는 스텁 — 가드가 서비스 호출 전에 차단하는지 검증용."""

    def __init__(self) -> None:
        self.called_with: list[str] = []

    def retrieve(self, query, *a, **k):
        self.called_with.append(query)
        raise AssertionError("가드를 통과하면 안 되는 입력인데 retrieve 가 호출됨")


@pytest.mark.parametrize("bad", ["", "   ", "\n\t "])
def test_empty_query_blocked_before_service(bad):
    """빈/공백 질의는 서비스 호출 없이 명시 메시지(garbage 조문 반환 방지)."""
    svc = _SpyService()
    tool = make_legal_tool(svc)
    content, artifact = _call(tool, bad)
    assert "비어" in content and artifact is None
    assert svc.called_with == []   # retrieve 미호출


def test_overlong_query_blocked_before_service():
    """초장문은 임베딩 500 을 유발 → '일시적 오류 재시도' 오안내 대신 명시적으로 거른다."""
    svc = _SpyService()
    tool = make_legal_tool(svc)
    content, artifact = _call(tool, "가" * 2001)
    assert "너무 깁니다" in content and artifact is None
    assert svc.called_with == []


def test_service_error_is_isolated():
    """서비스 내부 예외(엔드포인트/스택)는 LLM·FE 에 노출 금지 — generic 메시지."""
    class _Boom:
        def retrieve(self, q, *a, **k):
            raise ConnectionError("secret-host:6333 down\nTraceback ...")

    tool = make_legal_tool(_Boom())
    content, artifact = _call(tool, "건폐율")
    assert "secret-host" not in content and "Traceback" not in content
    assert "오류" in content and artifact is None
