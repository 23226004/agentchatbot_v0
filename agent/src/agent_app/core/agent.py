"""ReActAgent — 실행 오케스트레이션.

config → LLM → tools → memory → graph 를 조립하고, run()/stream() 으로 노출한다.
상위 계층(backend)은 이 클래스만 알면 된다.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

from agent_app.core import observability as _obs
from agent_app.core.config import Settings
from agent_app.core.llm import build_llm
from agent_app.graph import build_react_graph
from agent_app.memory import build_checkpointer
from agent_app.prompts import SYSTEM_PROMPT
from agent_app.tools import get_tools


class ReActAgent:
    def __init__(
        self,
        settings: Settings | None = None,
        checkpointer: Any = None,
        retrieval_service: Any = None,
        require_approval: bool = False,
    ) -> None:
        self.settings = settings or Settings.from_env()
        self.llm = build_llm(self.settings)
        self.tools = get_tools(retrieval_service)
        # checkpointer 외부주입(conversation-store §6): 미주입 시 인메모리(MemorySaver) 기본.
        # backend 합성 루트가 PostgresSaver 를 주입해 영속 활성화.
        self.checkpointer = checkpointer or build_checkpointer()
        # require_approval: 도구 실행 전 승인 게이트(interrupt_before). 재개는 resume().
        self.graph = build_react_graph(
            self.llm, self.tools, SYSTEM_PROMPT, self.checkpointer,
            interrupt_before=["tools"] if require_approval else None,
        )

    @staticmethod
    def _input(message: str) -> dict[str, Any]:
        return {"messages": [{"role": "user", "content": message}]}

    @staticmethod
    def _config(thread_id: str) -> dict[str, Any]:
        # thread_id 별로 대화 메모리가 분리된다.
        # **recursion_limit 명시 필수**: 부분 config dict 를 넘기면 langgraph 가 기본값 25 를
        # 주입하지 않아 루프하는/적대적 LLM 이 무한 도구호출(비용·스레드 무제한)한다(교차검증 HIGH).
        cfg: dict[str, Any] = {"configurable": {"thread_id": thread_id}, "recursion_limit": 25}
        cfg.update(_obs.trace_config(thread_id))         # Langfuse 활성 시 callbacks+session 머지(비활성=무변)
        return cfg

    def run(self, message: str, thread_id: str = "default") -> str:
        """한 번에 실행하고 최종 답변 문자열만 반환한다."""
        try:
            result = self.graph.invoke(self._input(message), self._config(thread_id))
            return result["messages"][-1].content
        finally:
            _obs.flush()

    def stream(self, message: str, thread_id: str = "default") -> Iterator[dict[str, Any]]:
        """단계별 업데이트(사고/도구호출/도구결과)를 순차로 흘려보낸다."""
        try:
            yield from self.graph.stream(
                self._input(message), self._config(thread_id), stream_mode="updates"
            )
        finally:
            _obs.flush()          # pump 스레드 단명 컨텍스트 — 종결/close 시 트레이스 flush(유실 방지)

    def resume(self, thread_id: str = "default") -> Iterator[dict[str, Any]]:
        """승인 후 일시정지된 그래프를 재개한다(입력 None, 같은 thread_id). interrupt_before 용."""
        if not self.graph.get_state(self._config(thread_id)).next:
            return  # 일시정지(interrupt) 없으면 재개할 것 없음 — EmptyInputError 방지
        try:
            yield from self.graph.stream(
                None, self._config(thread_id), stream_mode="updates"
            )
        finally:
            _obs.flush()

    def reject_pending(self, thread_id: str = "default") -> None:
        """거절 시 일시정지된 그래프의 미결 도구호출(AIMessage.tool_calls)을 제거해 체크포인트를 정합화.

        제거하지 않으면 next=('tools',)·대응 ToolMessage 없는 AIMessage 가 영속돼, 같은 thread 로
        다음 run 시 INVALID_CHAT_HISTORY 로 thread 가 영구 잠긴다(교차검증 실버그). RemoveMessage 로
        매달린 tool_calls AIMessage 를 지우면 next=()로 깨끗이 초기화된다(실측).
        """
        from langchain_core.messages import RemoveMessage

        cfg = self._config(thread_id)
        msgs = self.graph.get_state(cfg).values.get("messages", [])
        pending = next((m for m in reversed(msgs) if getattr(m, "tool_calls", None)), None)
        if pending is not None and getattr(pending, "id", None):
            self.graph.update_state(cfg, {"messages": [RemoveMessage(id=pending.id)]})

    def clear_interrupted_tools(self, thread_id: str) -> None:
        """협조취소로 **재개 도중 중단**된 그래프의 진행중 도구 턴을 정리(XV D-1 poison 방지). 미커밋
        도구결과가 checkpoint 에 남으면 다음 턴 LLM 이 인용 → message_citations 링크 없는 citation 이라
        cite_forgery 로 thread 영구 사망.

        **방식 = REMOVE_ALL_MESSAGES + 도구턴 이전 prefix 를 content 로 재구성**(fork_state 동형). ID 기반
        제거(RemoveMessage by id)는 stream.close() 후 깨진다 — **ID 의존성을 피하는 게 핵심**:
          · ToolMessage 들까지 id 로 제거 → 다중도구는 미정착 재생성 ID 로 'ID 없음' ValueError(XV-2 결함).
          · AIMessage 만 제거 → 단일도구(close 후 next=('agent',))는 ToolMessage 가 캐스케이드 안 돼 고아
            잔류 → 그 결과 다음 턴 인용 시 다시 poison.
        content 재구성은 두 경우 모두 안정(probe 실증). 도구셀 제외(dual-body: transcript content_md 가 정본).
        다음 턴은 깨끗이 새로 시작(필요시 도구 재호출)."""
        from langchain_core.messages import AIMessage, HumanMessage, RemoveMessage
        from langgraph.graph.message import REMOVE_ALL_MESSAGES

        cfg = self._config(thread_id)
        msgs = self.graph.get_state(cfg).values.get("messages", [])
        if not any(getattr(m, "tool_calls", None) for m in msgs):
            return                                  # 진행중 도구턴 없음 — 정리 불요
        # **마지막 '완료 답변'(tool_calls 없는 AIMessage)까지만** 보존. 그 이후 = 취소된 in-flight 턴
        # (질문 Human + 도구 플러밍, 답변 없음) → 전부 drop. 취소턴의 Human 질문도 제외해야 반복 취소
        # 시 답변 없는 Human 이 checkpoint 에 무한 누적되지 않는다(XV-3 Medium). prefix 가 답변(AI)으로
        # 끝나 next=() 로 정리됨(매달린 Human 으로 끝나면 next=('agent',) 잔재가 생기던 것도 해소).
        cut = 0
        for i, m in enumerate(msgs):
            if m.__class__.__name__ == "AIMessage" and not getattr(m, "tool_calls", None):
                cut = i + 1
        prefix: list[Any] = []
        for m in msgs[:cut]:                         # 완료된 턴만(Human/답변AI) — 도구셀·취소턴 제외
            cls = m.__class__.__name__
            if cls == "HumanMessage":
                prefix.append(HumanMessage(content=m.content))
            elif cls == "AIMessage" and not getattr(m, "tool_calls", None):
                prefix.append(AIMessage(content=m.content))
        if not prefix:
            # 완료 답변이 하나도 없음(첫 턴부터 취소). **빈 checkpoint 는 should_continue 가
            # messages[-1] 에서 IndexError 로 크래시** → 첫 Human 하나만 앵커로 보존(REMOVE_ALL 이
            # 이전 것을 지우므로 반복 취소서도 단일 유지=누적 없음).
            first_h = next((m for m in msgs if m.__class__.__name__ == "HumanMessage"), None)
            if first_h is None:
                return                               # 메시지에 Human 자체가 없음(이론) — 정리 불요
            prefix = [HumanMessage(content=first_h.content)]
        self.graph.update_state(cfg, {"messages": [RemoveMessage(id=REMOVE_ALL_MESSAGES), *prefix]})

    def approve_partial(self, thread_id: str, approved_ids: set[str]) -> list[str]:
        """선택적 승인(per-tool Stage 2): 미결 AIMessage 에서 approved_ids 의 tool_call 만 남기고
        **같은 id 로 replace**(add_messages reducer 가 교체)한다. 이후 resume() 하면 ToolNode 가
        **승인분만** 실행한다 — probe 실증: next=('tools',) 보존·매달린 tool_call 0·다음 턴 INVALID 없음.
        거절된 tool_call_id 리스트를 반환한다. 전량 거절이면 reject_pending 으로 위임(매달린 AIMessage
        제거), 전량 승인이면 무수술(빈 거절 리스트). content 만 보존(additional_kwargs 의 raw tool_calls
        섀도잉 회피 — probe 가 content+tool_calls 만으로 유효 입증)."""
        from langchain_core.messages import AIMessage

        cfg = self._config(thread_id)
        msgs = self.graph.get_state(cfg).values.get("messages", [])
        pending = next((m for m in reversed(msgs) if getattr(m, "tool_calls", None)), None)
        if pending is None:
            return []
        approved = set(approved_ids)
        kept = [tc for tc in pending.tool_calls if tc.get("id") in approved]
        rejected = [tc.get("id") for tc in pending.tool_calls if tc.get("id") not in approved]
        if not kept:
            self.reject_pending(thread_id)            # 전량 거절
            return rejected
        if rejected:                                  # 부분 거절일 때만 교체(전량 승인은 무수술)
            self.graph.update_state(
                cfg, {"messages": [AIMessage(id=pending.id, content=pending.content, tool_calls=kept)]})
        return rejected

    def summarize(self, text: str) -> str:
        """대화 텍스트를 간결히 요약한다(ReAct 그래프 아닌 LLM 직접호출). 긴 컨텍스트 압축용."""
        from langchain_core.messages import HumanMessage, SystemMessage

        out = self.llm.invoke([
            SystemMessage(content=(
                "다음 대화를 한국어로 간결하게 요약하라. 핵심 질문·답변·인용 법령을 보존하고, "
                "이후 대화가 맥락을 이어갈 수 있도록 사실만 담아라. 군더더기·인사말 제외.")),
            HumanMessage(content=text),
        ])
        return out.content if isinstance(out.content, str) else str(out.content)

    def fork_state(self, new_thread_id: str, transcript_messages: list[dict]) -> None:
        """fork F-1 (conversation-store §6 D-1): 새 thread checkpoint 를 **transcript 정본**으로 시드.

        checkpoint 는 thread 스코프라 분기 후 새 thread 의 그래프가 부모 대화 맥락을 가지려면 명시
        시드가 필요하다. **transcript content_md 가 정본**(checkpoint AIMessage=초안, dual-body D-1)이고
        artifact(AnswerContext)는 직렬화로 깨지므로 제외 — user/agent 턴만 user→Human, agent→AI 로
        재구성해 주입한다(도구셀은 매달린 tool_calls 없는 content_md 만이라 제외해도 INVALID 없음).
        """
        from langchain_core.messages import AIMessage, HumanMessage

        lc: list[Any] = []
        for m in transcript_messages:
            role, content = m.get("role"), m.get("content_md") or ""
            if role == "user":
                lc.append(HumanMessage(content=content))
            elif role == "agent":
                lc.append(AIMessage(content=content))
            # tool 셀은 제외(최종 답변이 정보를 담고, 매달린 tool_calls 도 없음)
        if lc:
            self.graph.update_state(self._config(new_thread_id), {"messages": lc})
