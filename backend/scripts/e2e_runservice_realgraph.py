"""E2E 재현 — RunService를 **실제 LangGraph**(create_react_agent/build_react_graph)로 검증.

슬라이스8 다각도 교차검증에서 승격(scratchpad→repo). 페이크 stream 계약테스트가 못 잡은
HIGH 버그(AIMessage.content가 콘텐츠블록 list일 때 본문 누락+cite 위조검증 무력화)를
**실경로로** 봉인한다. LLM 서버 불요 — 스텁 ChatModel로 구동.

실행(langgraph+langchain_core 필요 → agent/.venv):
    REPO=/path/to/2026_06_20_Agent
    cd "$(mktemp -d)"  # 루트 디렉터리명=패키지명 섀도잉 회피
    PYTHONPATH="$REPO/agent:$REPO/backend/src:$REPO/legal_core/src" \
        "$REPO/agent/.venv/bin/python" "$REPO/backend/scripts/e2e_runservice_realgraph.py"

종료코드 0 = 전부 통과. 인프라(Qdrant/Fuseki) 불필요(RetrievalService는 페이크 주입).
"""

from __future__ import annotations

from typing import Any

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langgraph.prebuilt import create_react_agent

from legal_core import ids
from legal_core.schemas import AnswerContext, LawRef

from backend_app.services.run_service import DISCLAIMER, RunService


# --- 페이크 RetrievalService: 결정적 point.id 생성(하드코딩 금지) ---
def _ref(no: str, branch: int | None = None) -> LawRef:
    uri = ids.article_iri("001823", "20200101", no, branch)
    return LawRef(
        id=ids.point_id(uri), kind="law", title="건축법",
        ref=f"건축법 제{no}조", snippet=f"제{no}조 발췌", url="https://www.law.go.kr/",
        uri=uri, resource_id="001823", eff_date="2020-01-01", score=1.0,
        article_text=f"제{no}조 전체 본문",
    )


_REFS = [_ref("2"), _ref("4")]
_VALID_IDS = [r.id for r in _REFS]


class FakeService:
    def retrieve(self, query: str, as_of=None, k: int = 8) -> AnswerContext:
        return AnswerContext(articles=list(_REFS), query=query)


# --- 스텁 ChatModel: 1차=도구호출, 2차=최종답변(final_content 형태를 주입으로 바꿈) ---
class StubChat(BaseChatModel):
    """결정적 2턴 ReAct 스텁. 도구결과 도착 전=도구호출, 후=final_content 최종답변."""

    final_content: Any = ""

    @property
    def _llm_type(self) -> str:
        return "stub-chat"

    def _generate(self, messages, stop=None, run_manager=None, **kwargs) -> ChatResult:
        has_tool_result = any(m.__class__.__name__ == "ToolMessage" for m in messages)
        if not has_tool_result:
            msg = AIMessage(content="", tool_calls=[{
                "name": "search_legal", "args": {"query": "거실의 정의"},
                "id": "call_001", "type": "tool_call"}])
        else:
            msg = AIMessage(content=self.final_content)
        return ChatResult(generations=[ChatGeneration(message=msg)])

    def bind_tools(self, tools, **kwargs):
        return self


def _run(final_content: Any) -> list:
    from agent_app.prompts import SYSTEM_PROMPT
    from agent_app.tools.legal import make_legal_tool

    tool = make_legal_tool(FakeService())
    graph = create_react_agent(StubChat(final_content=final_content), [tool],
                               prompt=SYSTEM_PROMPT, checkpointer=None)

    class _Adapter:
        def stream(self, message, thread_id="default"):
            cfg = {"configurable": {"thread_id": thread_id}}
            yield from graph.stream(
                {"messages": [{"role": "user", "content": message}]},
                cfg, stream_mode="updates")

    # 인메모리 repo 대역(이 스크립트는 LangGraph→SSE 계약 검증용, DB 불요)
    return list(RunService(_Adapter(), _MemRepo()).run("거실이 뭐야?", thread_id="t1"))


class _MemRepo:
    """RunService 가 쓰는 repo 인터페이스의 인메모리 대역(seq 채번·no-op 영속)."""

    def __init__(self):
        self._n = 0

    def next_seq(self, _t):
        self._n += 1
        return self._n

    def open_run(self, _t):
        return "run-mem"

    def set_run_status(self, *_a, **_k):
        pass

    def try_transition(self, *_a, **_k):      # CAS 종결(running→status) — 메모리대역은 항상 성공
        return True

    def add_message(self, thread_id, *, seq=None, **_k):
        if seq is None:                       # seq 원자채번(RunService 가 seq=None 으로 호출)
            seq = self.next_seq(thread_id)
        return "m", seq

    def set_tool_result(self, *_a, **_k):
        pass

    def get_active_run(self, _t):
        return None

    def get_thread_citations(self, _t):
        return []

    def get_turn_root(self, _r):
        return None

    def set_pending_approval(self, *_a, **_k):
        pass

    def freeze_citation(self, *_a, **_k):
        pass


def _names(evs):
    return [e.event for e in evs]


def _completed(evs):
    return next(e for e in evs if e.event == "message.completed")


def main() -> int:
    cid = _VALID_IDS[0]

    # 1) 해피패스 — str content
    evs = _run(f"건축법상 거실은 ...이다 [[cite:{cid}]].")
    assert _names(evs) == ["run.started", "tool.call", "tool.result",
                           "citation.added", "citation.added",
                           "message.completed", "run.done"], _names(evs)
    done = _completed(evs)
    assert cid in done.data["text"] and "거실은" in done.data["text"]
    assert DISCLAIMER in done.data["text"] and "기준 시행일자: 2020-01-01" in done.data["text"]
    assert done.data["citations"] == [cid]
    print("[1] str-content happy path: OK")

    # 2) HIGH 회귀 — list(콘텐츠블록) content. str 가드만 있으면 본문 누락됐던 버그.
    evs = _run([{"type": "text", "text": f"건축법상 거실은 ...이다 [[cite:{cid}]]."}])
    done = _completed(evs)
    assert "거실은" in done.data["text"], "list-content 본문 누락(HIGH 회귀)"
    assert done.data["citations"] == [cid], "list-content cite 누락(HIGH 회귀)"
    print("[2] list-content body+cite preserved (HIGH regression): OK")

    # 3) 위조 — 검색결과에 없는 id → error 로 종결(뒤에 run.done 없음)
    evs = _run("근거 없는 단언 [[cite:not-real-id]].")
    names = _names(evs)
    assert names[-1] == "error" and "run.done" not in names, names
    err = next(e for e in evs if e.event == "error")
    assert err.data["reason"] == "cite_forgery"
    print("[3] forgery -> error terminal (no run.done): OK")

    # 4) 서버권위 줄 사칭 차단 — 초안이 가짜 시행일자/면책 써넣어도 strip
    evs = _run(f"⚖️ 기준 시행일자: 2099-01-01 (공식)\n\n거실 정의 [[cite:{cid}]].\n"
               f"※ 본 답변은 법률자문입니다(가짜).")
    text = _completed(evs).data["text"]
    assert "2099-01-01" not in text and "기준 시행일자: 2020-01-01" in text
    assert text.count("※ 본 답변은 법률자문") == 1
    print("[4] server-authority spoof stripped: OK")

    print("\nALL PASS (real LangGraph e2e)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
