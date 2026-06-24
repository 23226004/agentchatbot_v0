"""ReActAgent — 실행 오케스트레이션.

config → LLM → tools → memory → graph 를 조립하고, run()/stream() 으로 노출한다.
상위 계층(backend)은 이 클래스만 알면 된다.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

from src.core.config import Settings
from src.core.llm import build_llm
from src.graph import build_react_graph
from src.memory import build_checkpointer
from src.prompts import SYSTEM_PROMPT
from src.tools import get_tools


class ReActAgent:
    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or Settings.from_env()
        self.llm = build_llm(self.settings)
        self.tools = get_tools()
        self.checkpointer = build_checkpointer()
        self.graph = build_react_graph(
            self.llm, self.tools, SYSTEM_PROMPT, self.checkpointer
        )

    @staticmethod
    def _input(message: str) -> dict[str, Any]:
        return {"messages": [{"role": "user", "content": message}]}

    @staticmethod
    def _config(thread_id: str) -> dict[str, Any]:
        # thread_id 별로 대화 메모리가 분리된다.
        return {"configurable": {"thread_id": thread_id}}

    def run(self, message: str, thread_id: str = "default") -> str:
        """한 번에 실행하고 최종 답변 문자열만 반환한다."""
        result = self.graph.invoke(self._input(message), self._config(thread_id))
        return result["messages"][-1].content

    def stream(self, message: str, thread_id: str = "default") -> Iterator[dict[str, Any]]:
        """단계별 업데이트(사고/도구호출/도구결과)를 순차로 흘려보낸다."""
        yield from self.graph.stream(
            self._input(message), self._config(thread_id), stream_mode="updates"
        )
