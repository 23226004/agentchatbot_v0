"""승인 interrupt→resume 통합테스트 (슬라이스4) — 실제 LangGraph + 실제 RunService + 실제 PG.

interrupt_before=["tools"] 로 실제 그래프를 일시정지시키고, RunService 가 awaiting_approval 을
PG 에 영속한 뒤 resume(승인/거절)로 재개·종결하는 전 경로를 실증. 미가동 시 skip.
agent checkpoint 는 InMemorySaver(프로세스 내 resume 상태 보존) — transcript 는 실제 PG.
"""

from __future__ import annotations

import pytest

psycopg = pytest.importorskip("psycopg")
pytest.importorskip("langgraph")

from langchain_core.language_models import BaseChatModel  # noqa: E402
from langchain_core.messages import AIMessage  # noqa: E402
from langchain_core.outputs import ChatGeneration, ChatResult  # noqa: E402
from langgraph.checkpoint.memory import InMemorySaver  # noqa: E402
from langgraph.prebuilt import create_react_agent  # noqa: E402

from legal_core import ids  # noqa: E402
from legal_core.schemas import AnswerContext, LawRef  # noqa: E402

from backend_app.db import build_pool, run_migrations  # noqa: E402
from backend_app.repositories import ConversationRepository  # noqa: E402
from backend_app.services.run_service import RunService  # noqa: E402

# 고유 law_id(099001) — 다른 테스트(test_run_service_persist=001823)와 같은 조문 uri 로
# citation 전역충돌하지 않게(freeze=첫-동결 권위라 본문 다르면 테스트 간 오염).
_URI = ids.article_iri("099001", "20260227", 2)
_CID = ids.point_id(_URI)


@pytest.fixture(scope="module")
def repo():
    try:
        pool = build_pool()
        run_migrations(pool)
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"convstore postgres 미가동: {exc}")
    yield ConversationRepository(pool)
    pool.close()


class FakeService:
    def retrieve(self, query, as_of=None, k=8):
        ref = LawRef(id=_CID, kind="law", title="건축법", ref="건축법 제2조",
                     snippet="거실 발췌", url="https://www.law.go.kr/", uri=_URI,
                     resource_id="001823", eff_date="2026-02-27", score=1.0,
                     article_text="제2조 전체 본문 — 거실이란 ...")
        return AnswerContext(articles=[ref], query=query)


class StubChat(BaseChatModel):
    @property
    def _llm_type(self):
        return "stub"

    def _generate(self, messages, stop=None, run_manager=None, **kw):
        has_tool = any(m.__class__.__name__ == "ToolMessage" for m in messages)
        if not has_tool:
            m = AIMessage(content="", tool_calls=[{
                "name": "search_legal", "args": {"query": "거실"},
                "id": "call_1", "type": "tool_call"}])
        else:
            m = AIMessage(content=f"건축법상 거실은 ...이다 [[cite:{_CID}]].")
        return ChatResult(generations=[ChatGeneration(message=m)])

    def bind_tools(self, tools, **kw):
        return self


class _Adapter:
    """ReActAgent 대역 — interrupt_before=["tools"] 실제 그래프 + stream/resume."""

    def __init__(self):
        from agent_app.tools.legal import make_legal_tool
        self.g = create_react_agent(
            StubChat(), [make_legal_tool(FakeService())],
            checkpointer=InMemorySaver(), interrupt_before=["tools"])

    def _cfg(self, t):
        return {"configurable": {"thread_id": t}}

    def stream(self, message, thread_id="default"):
        yield from self.g.stream({"messages": [{"role": "user", "content": message}]},
                                 self._cfg(thread_id), stream_mode="updates")

    def resume(self, thread_id="default"):
        yield from self.g.stream(None, self._cfg(thread_id), stream_mode="updates")

    def reject_pending(self, thread_id="default"):
        from langchain_core.messages import RemoveMessage
        cfg = self._cfg(thread_id)
        msgs = self.g.get_state(cfg).values.get("messages", [])
        pend = next((m for m in reversed(msgs) if getattr(m, "tool_calls", None)), None)
        if pend is not None and getattr(pend, "id", None):
            self.g.update_state(cfg, {"messages": [RemoveMessage(id=pend.id)]})

    def clear_interrupted_tools(self, thread_id="default"):
        from langchain_core.messages import HumanMessage, RemoveMessage
        from langchain_core.messages import AIMessage as _AI
        from langgraph.graph.message import REMOVE_ALL_MESSAGES
        cfg = self._cfg(thread_id)
        msgs = self.g.get_state(cfg).values.get("messages", [])
        if not any(getattr(m, "tool_calls", None) for m in msgs):
            return
        cut = 0
        for i, m in enumerate(msgs):
            if m.__class__.__name__ == "AIMessage" and not getattr(m, "tool_calls", None):
                cut = i + 1
        prefix = []
        for m in msgs[:cut]:
            if m.__class__.__name__ == "HumanMessage":
                prefix.append(HumanMessage(content=m.content))
            elif m.__class__.__name__ == "AIMessage" and not getattr(m, "tool_calls", None):
                prefix.append(_AI(content=m.content))
        if not prefix:
            fh = next((m for m in msgs if m.__class__.__name__ == "HumanMessage"), None)
            if fh is None:
                return
            prefix = [HumanMessage(content=fh.content)]
        self.g.update_state(cfg, {"messages": [RemoveMessage(id=REMOVE_ALL_MESSAGES), *prefix]})

    def fork_state(self, new_thread_id, transcript_messages):
        from langchain_core.messages import AIMessage, HumanMessage
        lc = []
        for m in transcript_messages:
            if m["role"] == "user":
                lc.append(HumanMessage(content=m.get("content_md") or ""))
            elif m["role"] == "agent":
                lc.append(AIMessage(content=m.get("content_md") or ""))
        if lc:
            self.g.update_state(self._cfg(new_thread_id), {"messages": lc})


def test_real_fork_seeds_checkpoint_state(repo):
    """fork F-1: 분기 후 새 thread 의 그래프 checkpoint 가 부모 transcript(user/agent)로 시드되나."""
    from backend_app.services.run_service import RunService
    rs = RunService(_Adapter(), repo)
    tid = repo.create_thread("fork-src")
    # transcript 에 user/agent 메시지(런 없이 직접 구성)
    m_u, _ = repo.add_message(tid, role="user", content_md="거실이 뭐야?")
    m_a, _ = repo.add_message(tid, role="agent", content_md="거실은 ...이다.")
    new_id = rs.fork(tid, fork_point_message_id=m_a)        # repo fork + agent.fork_state
    # 새 thread 그래프 state 에 Human/AI 가 시드됐는지(분기 후 대화 맥락)
    seeded = rs.agent.g.get_state({"configurable": {"thread_id": new_id}}).values.get("messages", [])
    kinds = [m.__class__.__name__ for m in seeded]
    assert kinds == ["HumanMessage", "AIMessage"]
    assert "거실이 뭐야?" in seeded[0].content and "거실은" in seeded[1].content


def _pause(repo, rs, tid):
    evs = list(rs.run("거실이 뭐야?", thread_id=tid))
    assert [e.event for e in evs] == ["run.started", "tool.call", "approval.requested"]
    return evs


def test_real_interrupt_persists_awaiting(repo):
    tid = repo.create_thread("appr-pause")
    _pause(repo, RunService(_Adapter(), repo), tid)
    with repo.pool.connection() as conn:
        st = conn.execute("SELECT status::text FROM runs WHERE thread_id=%s", (tid,)).fetchone()[0]
        assert st == "awaiting_approval"
        appr = conn.execute(
            "SELECT approval_state, tool_result FROM messages "
            "WHERE thread_id=%s AND role='tool'", (tid,)).fetchone()
        assert appr[0] == "pending" and appr[1] is None      # 승인대기·결과 미도착


def test_real_approve_resumes_and_persists(repo):
    tid = repo.create_thread("appr-ok")
    rs = RunService(_Adapter(), repo)
    _pause(repo, rs, tid)
    evs = list(rs.resume(tid, approve=True))
    assert [e.event for e in evs] == ["tool.result", "citation.added",
                                      "message.completed", "run.done"]
    with repo.pool.connection() as conn:
        assert conn.execute("SELECT status::text FROM runs WHERE thread_id=%s",
                            (tid,)).fetchone()[0] == "completed"
        tool = conn.execute("SELECT approval_state, tool_result FROM messages "
                            "WHERE thread_id=%s AND role='tool'", (tid,)).fetchone()
        assert tool[0] == "approved" and tool[1] is not None   # 승인·결과 채워짐
        assert conn.execute("SELECT article_text FROM citations WHERE id=%s",
                            (_CID,)).fetchone() is not None     # 전문 동결
        ag = conn.execute("SELECT content_md FROM messages WHERE thread_id=%s AND role='agent'",
                          (tid,)).fetchone()[0]
        assert _CID in ag and "기준 시행일자" in ag


def test_real_reject_terminates(repo):
    tid = repo.create_thread("appr-no")
    rs = RunService(_Adapter(), repo)
    _pause(repo, rs, tid)
    evs = list(rs.resume(tid, approve=False))
    assert [e.event for e in evs] == ["run.done"] and evs[0].data["status"] == "rejected"
    with repo.pool.connection() as conn:
        assert conn.execute("SELECT status::text FROM runs WHERE thread_id=%s",
                            (tid,)).fetchone()[0] == "rejected"


def test_real_reject_then_reuse_thread_not_locked(repo):
    """교차검증 실버그 회귀: 거절 후 **같은 thread**(같은 checkpointer)로 새 run 이 가능해야.

    reject 가 그래프 미결 도구호출을 정리하지 않으면 다음 run 이 INVALID_CHAT_HISTORY 로 죽어
    thread 가 영구 잠긴다. _clear_rejected_graph(RemoveMessage)로 정합화됨을 실 LangGraph 로 봉인.
    """
    adapter = _Adapter()                                  # 같은 그래프·checkpointer 유지(영속성 모사)
    rs = RunService(adapter, repo)
    tid = repo.create_thread("appr-reuse")
    _pause(repo, rs, tid)
    list(rs.resume(tid, approve=False))                   # 거절 → 그래프 정리
    # reconcile 로 거절 run 의 잔여 active 가 없도록(이미 rejected=terminal). 새 run 시작.
    evs = list(rs.run("다른 질문", thread_id=tid))         # 같은 thread 재사용
    assert [e.event for e in evs] == ["run.started", "tool.call", "approval.requested"]
    # 새 턴이 INVALID_CHAT_HISTORY 없이 정상 일시정지(이전엔 error 로 잠겼음)
    assert "error" not in [e.event for e in evs]


# ── per-tool Stage 2: 선택적 실행 (실 LangGraph 상태수술) ──────────────────────────────
class TwoCallChat(BaseChatModel):
    """첫 턴: search_legal 2개 호출(call_1·call_2). 도구결과 본 뒤: 최종답변(cite)."""
    @property
    def _llm_type(self):
        return "twocall"

    def _generate(self, messages, stop=None, run_manager=None, **kw):
        has_tool = any(m.__class__.__name__ == "ToolMessage" for m in messages)
        if not has_tool:
            m = AIMessage(content="", tool_calls=[
                {"name": "search_legal", "args": {"query": "거실"}, "id": "call_1", "type": "tool_call"},
                {"name": "search_legal", "args": {"query": "건폐율"}, "id": "call_2", "type": "tool_call"},
            ])
        else:
            m = AIMessage(content=f"건축법상 거실은 ...이다 [[cite:{_CID}]].")
        return ChatResult(generations=[ChatGeneration(message=m)])

    def bind_tools(self, tools, **kw):
        return self


class _TwoToolAdapter(_Adapter):
    """2개 tool_call 을 내는 stub + per-tool 선택적 승인(approve_partial) 지원."""

    def __init__(self):
        from agent_app.tools.legal import make_legal_tool
        self.g = create_react_agent(
            TwoCallChat(), [make_legal_tool(FakeService())],
            checkpointer=InMemorySaver(), interrupt_before=["tools"])

    def approve_partial(self, thread_id, approved_ids):
        cfg = self._cfg(thread_id)
        msgs = self.g.get_state(cfg).values.get("messages", [])
        pend = next((m for m in reversed(msgs) if getattr(m, "tool_calls", None)), None)
        if pend is None:
            return []
        approved = set(approved_ids)
        kept = [tc for tc in pend.tool_calls if tc.get("id") in approved]
        rejected = [tc.get("id") for tc in pend.tool_calls if tc.get("id") not in approved]
        if not kept:
            self.reject_pending(thread_id)
            return rejected
        if rejected:
            self.g.update_state(cfg, {"messages": [
                AIMessage(id=pend.id, content=pend.content, tool_calls=kept)]})
        return rejected


def _pause2(rs, tid):
    evs = list(rs.run("거실이 뭐야?", thread_id=tid))
    # 2 tool_call → tool.call 2개 후 approval.requested(tools 2개 목록)
    assert [e.event for e in evs] == ["run.started", "tool.call", "tool.call", "approval.requested"]
    appr = evs[-1].data
    assert {t["id"] for t in appr["tools"]} == {"call_1", "call_2"}    # Stage1 노출
    return evs


def test_real_partial_approve_executes_only_selected(repo):
    """per-tool Stage 2: 2개 중 call_1 만 승인 → call_1 만 실행, call_2 거절 마킹. 실 LangGraph."""
    tid = repo.create_thread("appr-partial")
    rs = RunService(_TwoToolAdapter(), repo)
    _pause2(rs, tid)
    evs = list(rs.resume(tid, approved_ids=["call_1"]))
    kinds = [e.event for e in evs]
    # 거절(call_2) tool.result 먼저, 그 뒤 승인(call_1) 실행 → citation → 완료
    assert kinds == ["tool.result", "tool.result", "citation.added", "message.completed", "run.done"]
    rej = evs[0].data
    assert rej["id"] == "call_2" and rej.get("rejected") is True
    assert evs[1].data["id"] == "call_1" and not evs[1].data.get("rejected")
    with repo.pool.connection() as conn:
        assert conn.execute("SELECT status::text FROM runs WHERE thread_id=%s",
                            (tid,)).fetchone()[0] == "completed"
        rows = dict(conn.execute(
            "SELECT tool_call_id, approval_state FROM messages "
            "WHERE thread_id=%s AND role='tool'", (tid,)).fetchall())
        assert rows == {"call_1": "approved", "call_2": "rejected"}    # per-tool 정합
        c1 = conn.execute("SELECT tool_result FROM messages WHERE thread_id=%s AND tool_call_id='call_1'",
                          (tid,)).fetchone()[0]
        c2 = conn.execute("SELECT tool_result FROM messages WHERE thread_id=%s AND tool_call_id='call_2'",
                          (tid,)).fetchone()[0]
        assert c1 is not None and "거부" not in str(c1)          # call_1 실제 결과
        assert "거부" in str(c2)                                  # call_2 거절 마커
        ag = conn.execute("SELECT content_md FROM messages WHERE thread_id=%s AND role='agent'",
                          (tid,)).fetchone()[0]
        assert _CID in ag                                         # 승인분 결과로 최종답변·인용


def test_real_partial_approve_empty_is_reject(repo):
    """선택적 승인에서 아무것도 승인 안 함(approved_ids=[]) → 전량 거절과 동일(rejected 종결)."""
    tid = repo.create_thread("appr-partial-none")
    rs = RunService(_TwoToolAdapter(), repo)
    _pause2(rs, tid)
    evs = list(rs.resume(tid, approved_ids=[]))
    assert [e.event for e in evs] == ["run.done"] and evs[0].data["status"] == "rejected"
    with repo.pool.connection() as conn:
        assert conn.execute("SELECT status::text FROM runs WHERE thread_id=%s",
                            (tid,)).fetchone()[0] == "rejected"


def test_real_partial_then_reuse_thread_not_locked(repo):
    """부분 승인 후 같은 thread(같은 checkpointer)로 새 run 이 INVALID_CHAT_HISTORY 없이 가능."""
    adapter = _TwoToolAdapter()
    rs = RunService(adapter, repo)
    tid = repo.create_thread("appr-partial-reuse")
    _pause2(rs, tid)
    list(rs.resume(tid, approved_ids=["call_1"]))             # 부분 승인 완주
    evs = list(rs.run("다른 질문", thread_id=tid))            # 같은 thread 재사용
    assert "error" not in [e.event for e in evs]
    assert evs[0].event == "run.started"


def test_real_cancel_during_resume_no_citation_poison(repo):
    """XV D-1 회귀: 승인 재개 중 취소가 도구결과 청크에 걸리면 **clear_interrupted_tools** 로
    진행중 도구턴(미커밋 도구결과)을 checkpoint 에서 제거 → 다음 턴이 그걸 인용해 cite_forgery 로
    thread 가 영구 사망하지 않게 한다. + 미완 도구셀이 영구 pending 고아로 남지 않음(D-2)."""
    import threading
    adapter = _Adapter()                                     # 단일 search_legal(interrupt_before tools)
    rs = RunService(adapter, repo)
    tid = repo.create_thread("appr-cancel-clean")
    _pause(repo, rs, tid)
    run_id = repo.get_active_run(tid)[0]
    # 재개 drive 의 첫 청크(도구결과) 전에 취소 set → 그 청크에서 취소 감지 → 정리 발동
    ev = threading.Event(); ev.set(); rs._cancels[run_id] = ev
    evs = list(rs.resume(tid, approve=True))
    assert evs[-1].event == "run.done" and evs[-1].data["status"] == "interrupted"  # 취소 종결
    with repo.pool.connection() as conn:
        # D-2: 도구셀이 영구 pending 고아 아님(취소 마커로 종결)
        assert conn.execute("SELECT tool_result FROM messages WHERE thread_id=%s AND role='tool'",
                            (tid,)).fetchone()[0] is not None
    # ★ 다음 턴: 같은 thread 새 run 이 **cite_forgery poison 없이** 정상 진행(정리 전엔 error 로 사망).
    evs2 = list(rs.run("거실 재질의", thread_id=tid))
    assert "error" not in [e.event for e in evs2]            # poison 아님(핵심)
    assert evs2[0].event == "run.started"


def test_real_cancel_during_multitool_resume_no_thread_death(repo):
    """XV-2 회귀(다중도구가 가렸던 결함): 2도구 **전량 승인** 재개 중 취소 → 정리는 마지막 tool_calls
    AIMessage 1개만 제거(리듀서가 ToolMessage 캐스케이드)해야 한다. AIMessage+ToolMessage 전부 제거하면
    stream.close() 후 ToolMessage 의 미정착 ID 로 RemoveMessage 가 ValueError → 취소가 **error 로 둔갑**·
    정리 실패·다음 턴 INVALID_CHAT_HISTORY 로 thread 사망. 취소가 깨끗이 interrupted 되고 다음 턴 생존 확인."""
    import threading
    rs = RunService(_TwoToolAdapter(), repo)
    tid = repo.create_thread("appr-cancel-multitool")
    _pause2(rs, tid)
    run_id = repo.get_active_run(tid)[0]
    ev = threading.Event(); ev.set(); rs._cancels[run_id] = ev
    evs = list(rs.resume(tid, approved_ids=["call_1", "call_2"]))   # 전량 승인 → ≥2 tool 노드 재개
    # ★ error 가 아니라 interrupted 로 종결(결함이면 error)
    assert evs[-1].event == "run.done" and evs[-1].data["status"] == "interrupted"
    with repo.pool.connection() as conn:
        assert conn.execute("SELECT status::text FROM runs WHERE thread_id=%s",
                            (tid,)).fetchone()[0] == "interrupted"
    # 다음 턴: thread 사망 없이 정상(결함이면 INVALID_CHAT_HISTORY → error)
    evs2 = list(rs.run("다른 질문", thread_id=tid))
    assert "error" not in [e.event for e in evs2]
    assert evs2[0].event == "run.started"


def test_real_repeated_cancel_does_not_accumulate_checkpoint(repo):
    """XV-3 회귀: 반복 취소(첫 턴부터)가 checkpoint 에 답변 없는 Human 을 무한 누적시키지 않는다.
    cut-at-last-answer + 빈-prefix fallback(첫 Human 단일 앵커) — 매 취소 후 Human 1 유지, 크래시 없음.
    (빈 checkpoint 는 should_continue 가 messages[-1] 에서 IndexError 로 크래시했었음.)"""
    import threading
    adapter = _Adapter()
    rs = RunService(adapter, repo)
    tid = repo.create_thread("appr-cancel-repeat")
    cfg = {"configurable": {"thread_id": tid}}
    for i in range(4):
        _pause(repo, rs, tid)                                  # 새 도구턴 → 승인대기
        run_id = repo.get_active_run(tid)[0]
        ev = threading.Event(); ev.set(); rs._cancels[run_id] = ev
        evs = list(rs.resume(tid, approve=True))              # 취소(첫 청크서 감지)
        assert evs[-1].data["status"] == "interrupted"        # error 둔갑 아님
        ckpt = adapter.g.get_state(cfg).values.get("messages", [])
        humans = sum(1 for m in ckpt if m.__class__.__name__ == "HumanMessage")
        assert humans <= 1, f"취소 {i}: checkpoint Human 누적({humans})"   # 누적 0(앵커 1 이하)
