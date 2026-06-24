"""ReAct 그래프 — LangGraph 사전구축 create_react_agent 사용.

추론↔도구호출 루프(관찰→사고→행동)를 그래프로 컴파일한다.
"""

from __future__ import annotations

from langchain_core.language_models import BaseChatModel
from langchain_core.tools import BaseTool
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.prebuilt import create_react_agent


def build_react_graph(
    llm: BaseChatModel,
    tools: list[BaseTool],
    prompt: str,
    checkpointer: BaseCheckpointSaver | None = None,
):
    """ReAct 에이전트 그래프를 컴파일해 반환한다."""
    return create_react_agent(
        llm,
        tools,
        prompt=prompt,
        checkpointer=checkpointer,
    )
