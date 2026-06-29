"""API + 합성루트 통합테스트 (슬라이스5) — FastAPI TestClient + 라이브 PG + 스텁 agent.

부팅 시퀀스(run_migrations→PostgresSaver.setup→reconcile)는 TestClient lifespan 이 실행한다
(라이브 PG 필요 → 미가동 시 skip). agent 는 stub 주입(LLM 불요). citation 은 고유 law_id(099003).
"""

from __future__ import annotations

import pytest

pytest.importorskip("psycopg")
pytest.importorskip("fastapi")

from fastapi.testclient import TestClient  # noqa: E402

from legal_core import ids  # noqa: E402
from legal_core.schemas import AnswerContext, LawRef  # noqa: E402

from backend_app.api import create_app  # noqa: E402
from backend_app.db import build_pool  # noqa: E402

_URI = ids.article_iri("099003", "20260227", 2)
_CID = ids.point_id(_URI)


@pytest.fixture(scope="module", autouse=True)
def _require_pg():
    try:
        p = build_pool(); p.close()
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"convstore postgres 미가동: {exc}")


class AIMsg:
    def __init__(self, content="", tool_calls=None):
        self.content = content
        self.tool_calls = tool_calls or []


class ToolMessage:
    def __init__(self, tool_call_id, content, artifact=None):
        self.tool_call_id, self.content, self.artifact = tool_call_id, content, artifact


class _Interrupt:
    def __init__(self, value): self.value = value


def _ref():
    return LawRef(id=_CID, kind="law", title="건축법", ref="건축법 제2조",
                  snippet="발췌", url="https://www.law.go.kr/", uri=_URI,
                  resource_id="099003", eff_date="2026-02-27", score=1.0,
                  article_text="제2조 전체 — 거실이란 ...")


def _tool_chunk():
    return {"tools": {"messages": [ToolMessage(
        "c1", "법령 텍스트", artifact=AnswerContext(articles=[_ref()], query="거실"))]}}


def _call_chunk():
    return {"agent": {"messages": [AIMsg(tool_calls=[
        {"id": "c1", "name": "search_legal", "args": {"query": "거실"}}])]}}


def _final_chunk():
    return {"agent": {"messages": [AIMsg(content=f"건축법상 거실은 ...이다 [[cite:{_CID}]].")]}}


import types

_SETTINGS = types.SimpleNamespace(llm_model="qwen3.6-35b-a3b", provider="compatible")


class RunAgent:
    """도구 1회 후 완주(승인 없음)."""
    settings = _SETTINGS                              # GET /models 노출용
    def stream(self, message, thread_id="default"):
        yield _call_chunk(); yield _tool_chunk(); yield _final_chunk()
    def resume(self, thread_id="default"):
        yield from ()
    def reject_pending(self, thread_id="default"):
        pass
    def summarize(self, text):                        # 스텁: LLM 대신 결정적 요약
        return f"[요약] {len(text)}자 대화"


class ApprovalAgent:
    """도구 전 interrupt(승인대기), resume 시 도구실행→완주."""
    def stream(self, message, thread_id="default"):
        yield _call_chunk()
        yield {"__interrupt__": (_Interrupt("승인?"),)}
    def resume(self, thread_id="default"):
        yield _tool_chunk(); yield _final_chunk()
    def reject_pending(self, thread_id="default"):
        pass


def _client(agent):
    return TestClient(create_app(agent_factory=lambda cp: agent))


def _events(resp):
    """SSE 응답 본문에서 event 이름 순서 추출."""
    return [ln[len("event: "):] for ln in resp.text.splitlines() if ln.startswith("event: ")]


def test_create_thread_and_empty_history():
    with _client(RunAgent()) as c:
        tid = c.post("/threads", json={"title": "t"}).json()["id"]
        msgs = c.get(f"/threads/{tid}/messages").json()["messages"]
        assert msgs == []


def _wait_status(c, thread_id, statuses, timeout=5.0):
    """백그라운드 run 의 DB status 가 statuses 중 하나가 될 때까지 폴링(디커플 — run은 스레드서 완주)."""
    import time
    repo = c.app.state.repo
    deadline = time.time() + timeout
    last = None
    while time.time() < deadline:
        with repo.pool.connection() as conn:
            row = conn.execute("SELECT status::text FROM runs WHERE thread_id=%s "
                               "ORDER BY started_at DESC LIMIT 1", (thread_id,)).fetchone()
        last = row[0] if row else None
        if last in statuses:
            return last
        time.sleep(0.03)
    raise AssertionError(f"run status {last} not in {statuses} within {timeout}s")


def test_decoupled_post_returns_run_id_then_stream_tails():
    """디커플: POST→{run_id}(즉시), GET /runs/{id}/stream→SSE tail(terminal까지). 이력 영속."""
    with _client(RunAgent()) as c:
        tid = c.post("/threads", json={}).json()["id"]
        r = c.post(f"/threads/{tid}/messages", json={"message": "거실이 뭐야?"})
        assert r.status_code == 200
        run_id = r.json()["run_id"]
        s = c.get(f"/runs/{run_id}/stream")
        assert s.headers["content-type"].startswith("text/event-stream")
        assert _events(s) == ["run.started", "tool.call", "tool.result",
                              "citation.added", "message.completed", "run.done"]
        roles = [m["role"] for m in c.get(f"/threads/{tid}/messages").json()["messages"]]
        assert roles == ["user", "tool", "agent"]   # 사용자 질문 + 도구셀 + 답변


def test_concurrent_run_returns_409():
    with _client(ApprovalAgent()) as c:
        tid = c.post("/threads", json={}).json()["id"]
        c.post(f"/threads/{tid}/messages", json={"message": "q1"})
        _wait_status(c, tid, {"awaiting_approval"})                  # q1 이 승인대기까지
        r2 = c.post(f"/threads/{tid}/messages", json={"message": "q2"})
        assert r2.status_code == 409


def test_approve_flow_resumes_to_completion():
    with _client(ApprovalAgent()) as c:
        tid = c.post("/threads", json={}).json()["id"]
        run_id = c.post(f"/threads/{tid}/messages", json={"message": "q"}).json()["run_id"]
        _wait_status(c, tid, {"awaiting_approval"})
        c.post(f"/threads/{tid}/approve", json={"approve": True})
        _wait_status(c, tid, {"completed"})
        # 같은 run_id 스트림이 일시정지를 건너 resume 이벤트까지 연속(terminal까지 replay)
        assert _events(c.get(f"/runs/{run_id}/stream")) == [
            "run.started", "tool.call", "approval.requested",
            "tool.result", "citation.added", "message.completed", "run.done"]
        assert any(m["role"] == "agent" for m in c.get(f"/threads/{tid}/messages").json()["messages"])


def test_approve_without_awaiting_returns_409():
    with _client(RunAgent()) as c:
        tid = c.post("/threads", json={}).json()["id"]
        assert c.post(f"/threads/{tid}/approve", json={"approve": True}).status_code == 409


# ── per-tool Stage 2: 선택적 실행 (HTTP→RunManager→RunService approved_ids 배선) ──────────
def _call_chunk2():
    return {"agent": {"messages": [AIMsg(tool_calls=[
        {"id": "c1", "name": "search_legal", "args": {"query": "거실"}},
        {"id": "c2", "name": "search_legal", "args": {"query": "건폐율"}}])]}}


class TwoToolApprovalAgent:
    """2개 도구 호출 전 interrupt. approve_partial→승인분만 resume 실행(페이크 surgery)."""
    def __init__(self):
        self._approved = None
    def stream(self, message, thread_id="default"):
        yield _call_chunk2()
        yield {"__interrupt__": (_Interrupt("승인?"),)}
    def approve_partial(self, thread_id, approved_ids):
        self._approved = set(approved_ids)
        return [t for t in ("c1", "c2") if t not in self._approved]
    def resume(self, thread_id="default"):
        if self._approved and "c1" in self._approved:
            yield _tool_chunk()                       # c1 결과(artifact→citation)
        yield _final_chunk()
    def reject_pending(self, thread_id="default"):
        pass


def test_partial_approve_executes_selected_via_http():
    """c1 만 승인 → c1 실행·c2 거절. 같은 run_id 스트림이 거절·승인 결과를 연속 replay, DB per-tool 정합."""
    with _client(TwoToolApprovalAgent()) as c:
        tid = c.post("/threads", json={}).json()["id"]
        run_id = c.post(f"/threads/{tid}/messages", json={"message": "q"}).json()["run_id"]
        _wait_status(c, tid, {"awaiting_approval"})
        assert c.post(f"/threads/{tid}/approve", json={"approved": ["c1"]}).status_code == 200
        _wait_status(c, tid, {"completed"})
        assert _events(c.get(f"/runs/{run_id}/stream")) == [
            "run.started", "tool.call", "tool.call", "approval.requested",
            "tool.result", "tool.result", "citation.added", "message.completed", "run.done"]
        with c.app.state.repo.pool.connection() as conn:
            rows = dict(conn.execute(
                "SELECT tool_call_id, approval_state FROM messages "
                "WHERE thread_id=%s AND role='tool'", (tid,)).fetchall())
        assert rows == {"c1": "approved", "c2": "rejected"}        # per-tool 정합


def test_partial_approve_rejects_malformed_approved_422():
    """approved 는 문자열 리스트만 — 비리스트/비문자열 원소는 422(엔드포인트 가드). awaiting 보존."""
    with _client(TwoToolApprovalAgent()) as c:
        tid = c.post("/threads", json={}).json()["id"]
        c.post(f"/threads/{tid}/messages", json={"message": "q"})
        _wait_status(c, tid, {"awaiting_approval"})
        assert c.post(f"/threads/{tid}/approve", json={"approved": "c1"}).status_code == 422
        assert c.post(f"/threads/{tid}/approve", json={"approved": [1, 2]}).status_code == 422
        # 전량 무효 id(대기 c1·c2 와 교집합 0) → 422(silent 전량거절 방지)
        assert c.post(f"/threads/{tid}/approve",
                      json={"approved": ["nope", "cal_1"]}).status_code == 422
        # 422 는 처리 전 반려 → 여전히 승인대기(소실 없음). 빈 리스트는 의도적 거절이라 422 아님.
        assert _wait_status(c, tid, {"awaiting_approval"}) == "awaiting_approval"


def test_history_citations_restorable():
    """이력 인용 복원: run 후 GET /citations 가 인용 조문을 돌려줘야(content_md 의 [[cite]] 렌더용)."""
    with _client(RunAgent()) as c:
        tid = c.post("/threads", json={}).json()["id"]
        c.post(f"/threads/{tid}/messages", json={"message": "거실?"})
        _wait_status(c, tid, {"completed"})
        cits = c.get(f"/threads/{tid}/citations").json()["citations"]
        assert any(str(x["id"]) == _CID and x["article_text"] for x in cits)   # 전문 포함


def test_message_validation_422():
    with _client(RunAgent()) as c:
        tid = c.post("/threads", json={}).json()["id"]
        assert c.post(f"/threads/{tid}/messages", json={"message": 123}).status_code == 422   # str 아님
        assert c.post(f"/threads/{tid}/messages", json={"message": "  "}).status_code == 422   # 공백
        assert c.post(f"/threads/{tid}/messages", json={"message": "x" * 100_001}).status_code == 422
        # NUL → 422(PG text 불가·자가-DoS 표면 차단, 교차검증 회귀)
        assert c.post(f"/threads/{tid}/messages", json={"message": "hi\x00x"}).status_code == 422


def test_fork_creates_branch_with_ancestor_prefix():
    """분기: fork_point에서 새 thread 생성 → 부모 prefix(≤fork_point) 가시(참조모델). 잘못된 fp→404/422."""
    import uuid
    with _client(RunAgent()) as c:
        tid = c.post("/threads", json={}).json()["id"]
        c.post(f"/threads/{tid}/messages", json={"message": "q"})
        _wait_status(c, tid, {"completed"})
        agent_msg = next(m for m in c.get(f"/threads/{tid}/messages").json()["messages"]
                         if m["role"] == "agent")
        r = c.post(f"/threads/{tid}/fork", json={"fork_point_message_id": agent_msg["id"]})
        assert r.status_code == 200
        new_tid = r.json()["thread_id"]
        new_roles = [m["role"] for m in c.get(f"/threads/{new_tid}/messages").json()["messages"]]
        assert new_roles == ["user", "tool", "agent"]    # 부모 prefix 상속(메시지 복사 아님, 가시성)
        assert c.post(f"/threads/{tid}/fork",
                      json={"fork_point_message_id": str(uuid.uuid4())}).status_code == 404
        assert c.post(f"/threads/{tid}/fork", json={"fork_point_message_id": "bad"}).status_code == 422


def test_summarize_creates_and_lists_summary():
    with _client(RunAgent()) as c:
        tid = c.post("/threads", json={}).json()["id"]
        c.post(f"/threads/{tid}/messages", json={"message": "건축법 거실?"})
        _wait_status(c, tid, {"completed"})
        res = c.post(f"/threads/{tid}/summarize", json={}).json()
        assert res["content_md"] and res["covers_from_seq"] is not None and res["covers_to_seq"] is not None
        sums = c.get(f"/threads/{tid}/summaries").json()["summaries"]
        assert len(sums) == 1 and sums[0]["content_md"] == res["content_md"]
        # 빈 thread(대화 없음) → 422
        empty = c.post("/threads", json={}).json()["id"]
        assert c.post(f"/threads/{empty}/summarize", json={}).status_code == 422


def test_summarize_seq_validation_and_llm_error_graceful():
    """음수 seq→422, LLM 오류(거대 대화·컨텍스트 초과)→불투명 500 대신 graceful 502(교차검증)."""
    with _client(RunAgent()) as c:
        tid = c.post("/threads", json={}).json()["id"]
        c.post(f"/threads/{tid}/messages", json={"message": "q"}); _wait_status(c, tid, {"completed"})
        assert c.post(f"/threads/{tid}/summarize", json={"from_seq": -1}).status_code == 422

    class FailSummAgent(RunAgent):
        def summarize(self, text):
            raise RuntimeError("context overflow")   # qwen 400 모사

    with _client(FailSummAgent()) as c:
        tid = c.post("/threads", json={}).json()["id"]
        c.post(f"/threads/{tid}/messages", json={"message": "q"}); _wait_status(c, tid, {"completed"})
        assert c.post(f"/threads/{tid}/summarize", json={}).status_code == 502


def test_settings_crud():
    with _client(RunAgent()) as c:
        c.put("/settings", json={"model": "qwen3.6", "server_url": "http://x:8080", "theme": "dark"})
        s = c.get("/settings").json()
        assert s["model"] == "qwen3.6" and s["theme"] == "dark" and s["scope"] == "global"
        c.put("/settings", json={"theme": "light"})            # 갱신
        assert c.get("/settings").json()["theme"] == "light"
        assert c.put("/settings", json={"theme": 123}).status_code == 422   # 타입검증


def test_models_lists_configured_agent_model():
    with _client(RunAgent()) as c:
        models = c.get("/models").json()["models"]
        assert any(m["id"] == "qwen3.6-35b-a3b" and m["provider"] == "compatible" for m in models)


def test_messages_grouped_by_turn_parent():
    """parent_id(§3.2): 도구셀·답변이 그 턴의 user 메시지(루트) 아래로 그룹핑."""
    with _client(RunAgent()) as c:
        tid = c.post("/threads", json={}).json()["id"]
        c.post(f"/threads/{tid}/messages", json={"message": "q"})
        _wait_status(c, tid, {"completed"})
        msgs = c.get(f"/threads/{tid}/messages").json()["messages"]
        user = next(m for m in msgs if m["role"] == "user")
        children = [m for m in msgs if m["role"] in ("tool", "agent")]
        assert children and all(m["parent_id"] == user["id"] for m in children)
        assert user["parent_id"] is None                       # 루트는 부모 없음


def test_create_thread_input_validation_422():
    """POST /threads 비-UUID owner_id → 422(500 누수 방지). SQLi는 파라미터화로 무해하나 위생."""
    with _client(RunAgent()) as c:
        assert c.post("/threads", json={"owner_id": "' OR 1=1--"}).status_code == 422
        assert c.post("/threads", json={"title": 123}).status_code == 422
        import uuid
        assert c.post("/threads", json={"owner_id": str(uuid.uuid4())}).status_code == 200


def test_nonexistent_thread_returns_404_not_500():
    """미존재/잘못된 thread_id → 404(500 누수 아님)."""
    import uuid
    with _client(RunAgent()) as c:
        ghost = str(uuid.uuid4())
        assert c.get(f"/threads/{ghost}/messages").status_code == 404
        assert c.post(f"/threads/{ghost}/messages", json={"message": "q"}).status_code == 404
        assert c.get("/threads/not-a-uuid/messages").status_code == 404   # 잘못된 UUID 도 404


def test_runmanager_terminal_guard_no_hang():
    """pump 의 gen 이 terminal(run.done/error) 없이 끝나도 stream 이 합성 terminal 로 종료(hang 방지).

    committed=False(외부 sweep) 등으로 RunService 가 terminal 못 내는 경로의 stream 무한대기 회귀.
    """
    import asyncio

    from backend_app.api.run_manager import RunManager
    from backend_app.services.run_service import RunEvent

    class MemRepo:
        """내구 로그 인메모리 대역(G4) — append/tail·get_run·next_seq 만."""
        def __init__(self): self.ev = []; self._seq = 2; self.status = "running"
        def append_run_event(self, thread_id, run_id, seq, event, data):
            self.ev.append({"seq": seq, "event": event, "data": data})
        def get_run_events_after(self, run_id, after_seq=-1, limit=1000):
            return [e for e in list(self.ev) if e["seq"] > after_seq]
        def get_run(self, run_id): return ("t1", self.status)
        def next_seq(self, thread_id): self._seq += 1; return self._seq

    class StubRS:
        def __init__(self): self.repo = MemRepo()

        def run(self, message, thread_id, model=None):
            yield RunEvent("run.started", {"run_id": "r1"}, 1)
            yield RunEvent("message.completed", {"text": "x"}, 2)  # terminal 없음(gen 정상 종료)

    async def go():
        mgr = RunManager(StubRS())
        rid = await mgr.start("t1", "q")
        evs = [ev.event async for ev in mgr.stream(rid)]
        mgr.shutdown()
        return evs

    evs = asyncio.run(asyncio.wait_for(go(), timeout=5))   # hang 이면 TimeoutError 로 실패
    # pump 가 terminal 없이 끝남 → DB(running) 이라 합성 error 를 로그에 영속 → stream 이 그걸로 종료.
    assert evs == ["run.started", "message.completed", "error"]   # 합성 error 로 종료


def test_boot_sequence_created_checkpoint_tables():
    """합성루트 lifespan 이 PostgresSaver.setup()을 호출해 checkpoint* 테이블이 존재해야(배포게이트)."""
    with _client(RunAgent()) as c:
        pool = c.app.state.pool
        with pool.connection() as conn:
            names = [r[0] for r in conn.execute(
                "SELECT tablename FROM pg_tables WHERE schemaname='public'").fetchall()]
        assert {"threads", "runs", "messages", "citations"} <= set(names)   # transcript
        assert any(n.startswith("checkpoint") for n in names)               # checkpoint*


def test_cors_preflight_allows_fe_origin():
    """CORS: FE(:5180) 프리플라이트(OPTIONS)에 allow-origin/credentials 회신 — 없으면 브라우저 차단."""
    with _client(RunAgent()) as c:
        r = c.options("/threads", headers={
            "Origin": "http://localhost:5180",
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "content-type",
        })
        assert r.status_code in (200, 204)
        assert r.headers.get("access-control-allow-origin") == "http://localhost:5180"
        assert r.headers.get("access-control-allow-credentials") == "true"


def test_cors_disallowed_origin_not_reflected():
    """허용 안 된 origin 은 allow-origin 으로 반사되지 않아야(credentials 모드라 와일드카드 불가)."""
    with _client(RunAgent()) as c:
        r = c.options("/threads", headers={
            "Origin": "http://evil.example",
            "Access-Control-Request-Method": "POST",
        })
        assert r.headers.get("access-control-allow-origin") != "http://evil.example"


def test_list_threads_returns_created(owner=None):
    """GET /threads: 생성한 스레드가 목록에 최근순으로 (FE 사이드바, §4.1)."""
    with _client(RunAgent()) as c:
        a = c.post("/threads", json={"title": "first"}).json()["id"]
        b = c.post("/threads", json={"title": "second"}).json()["id"]
        items = c.get("/threads").json()["threads"]
        ids = [t["id"] for t in items]
        assert a in ids and b in ids
        # 각 항목이 FE 가 쓸 키를 포함
        row = next(t for t in items if t["id"] == b)
        assert set(row) >= {"id", "title", "created_at", "updated_at"}


def test_list_threads_rejects_bad_owner_422():
    with _client(RunAgent()) as c:
        assert c.get("/threads", params={"owner_id": "not-a-uuid"}).status_code == 422


# ── interrupt(중지) ──────────────────────────────────────────────────────────
import threading as _threading  # noqa: E402


class GateAgent:
    """첫 청크를 gate 가 열릴 때까지 막는다 — running 상태에서 중지 테스트용."""
    def __init__(self):
        self.gate = _threading.Event()
    def stream(self, message, thread_id="default"):
        self.gate.wait(timeout=5)
        yield _final_chunk()
    def resume(self, thread_id="default"):
        yield from ()


def test_interrupt_running_returns_interrupting_then_terminates():
    agent = GateAgent()
    with _client(agent) as c:
        tid = c.post("/threads", json={}).json()["id"]
        run_id = c.post(f"/threads/{tid}/messages", json={"message": "q"}).json()["run_id"]
        _wait_status(c, tid, ("running",))             # 펌프가 gate 에서 대기 = running
        r = c.post(f"/runs/{run_id}/interrupt")
        assert r.status_code == 200 and r.json()["status"] == "interrupting"
        agent.gate.set()                               # 청크 1개 흘려 취소 검사 트리거
        assert _wait_status(c, tid, ("interrupted",)) == "interrupted"
        assert _events(c.get(f"/runs/{run_id}/stream"))[-1] == "run.done"


def test_interrupt_awaiting_approval_terminates_and_unlocks_thread():
    with _client(ApprovalAgent()) as c:
        tid = c.post("/threads", json={}).json()["id"]
        run_id = c.post(f"/threads/{tid}/messages", json={"message": "q"}).json()["run_id"]
        _wait_status(c, tid, ("awaiting_approval",))
        r = c.post(f"/runs/{run_id}/interrupt")
        assert r.status_code == 200 and r.json()["status"] == "interrupted"
        assert _wait_status(c, tid, ("interrupted",)) == "interrupted"
        # 스트림에 종료 이벤트가 푸시되어 run.done 으로 끝남
        assert _events(c.get(f"/runs/{run_id}/stream"))[-1] == "run.done"
        # 터미널이라 thread 잠금 해제 — 새 run 시작 가능(409 아님)
        assert c.post(f"/threads/{tid}/messages", json={"message": "다시"}).status_code == 200


def test_interrupt_unknown_run_404():
    import uuid as _u
    with _client(RunAgent()) as c:
        assert c.post(f"/runs/{_u.uuid4()}/interrupt").status_code == 404
        assert c.post("/runs/not-a-uuid/interrupt").status_code == 422


def test_interrupt_completed_run_409():
    with _client(RunAgent()) as c:
        tid = c.post("/threads", json={}).json()["id"]
        run_id = c.post(f"/threads/{tid}/messages", json={"message": "q"}).json()["run_id"]
        _wait_status(c, tid, ("completed", "error"))
        r = c.post(f"/runs/{run_id}/interrupt")
        assert r.status_code == 409


def test_nul_rejected_on_title_and_settings_422():
    """NUL 차단 일관성(적대검증): /messages 뿐 아니라 title·settings 도 422(PG DataError 500 방지)."""
    with _client(RunAgent()) as c:
        assert c.post("/threads", json={"title": "a\x00b"}).status_code == 422
        assert c.put("/settings", json={"model": "m\x00"}).status_code == 422
        assert c.put("/settings", json={"theme": "t\x00"}).status_code == 422


def test_oversize_body_rejected_413_before_parse():
    """G3 body-DoS: 필드 캡은 파싱 후라 거대 JSON body 자체는 무방비였음 — body 상한(기본 1MB)이 파싱
    전 413 으로 차단(Content-Length). 정상 크기는 통과."""
    hdr = {"content-type": "application/json"}
    with _client(RunAgent()) as c:
        big = '{"title": "' + "x" * 2_000_000 + '"}'        # ~2MB body (>1MB 상한)
        r = c.post("/threads", content=big, headers=hdr)
        assert r.status_code == 413
        # ★ XV C 회귀: cross-origin 413 도 CORS 헤더(ACAO)를 받아야 브라우저가 차단 안 함(미들웨어 순서)
        ro = c.post("/threads", content=big,
                    headers={**hdr, "origin": "http://localhost:5180"})
        assert ro.status_code == 413
        assert ro.headers.get("access-control-allow-origin") == "http://localhost:5180"
        # 정상 크기 요청은 영향 없음
        assert c.post("/threads", json={"title": "ok"}).status_code == 200
        assert c.put("/settings", json={"model": "m"}).status_code == 200


def test_log_level_invalid_falls_back_no_boot_crash_g6():
    """G6/적대: LOG_LEVEL 무효값(GARBAGE·숫자)이 setLevel ValueError 로 부팅 크래시 내지 않고 INFO 폴백."""
    import logging
    import os

    from backend_app.api.app import configure_logging
    prev = os.environ.get("LOG_LEVEL")
    try:
        for bad in ("GARBAGE", "10", "  "):
            os.environ["LOG_LEVEL"] = bad
            configure_logging()                            # 크래시 없어야
            assert logging.getLogger("conversation").level == logging.INFO   # INFO 폴백
        os.environ["LOG_LEVEL"] = "warning"                # 유효(소문자) 는 적용
        configure_logging()
        assert logging.getLogger("conversation").level == logging.WARNING
    finally:
        if prev is None:
            os.environ.pop("LOG_LEVEL", None)
        else:
            os.environ["LOG_LEVEL"] = prev
        configure_logging()                                # 원복(INFO 등)


def test_stats_endpoint_exposes_run_counts_and_pool_g6():
    """G6 메트릭: /stats 가 run 상태별 수(DB) + 연결풀 통계(active/idle/waiting)를 노출."""
    with _client(RunAgent()) as c:
        c.post("/threads", json={"title": "t"})                # 데이터 약간 생성
        s = c.get("/stats")
        assert s.status_code == 200
        body = s.json()
        assert isinstance(body["runs"], dict)                  # 상태별 run 수(없으면 빈 dict)
        assert isinstance(body["pool"], dict) and "pool_size" in body["pool"]   # psycopg_pool get_stats


def test_surrogate_rejected_422_not_500():
    """G3/적대: lone surrogate(PG UTF-8 인코딩 불가)는 NUL 과 동급 — 422 로 거른다(안 막으면 title/settings
    는 500, message 는 무성 유실). 공격 벡터 = **JSON 이스케이프 `\\ud800`**(서버 파서가 lone surrogate 로
    디코딩) → raw content 로 주입. 세 저장-텍스트 경로 공통."""
    hdr = {"content-type": "application/json"}
    with _client(RunAgent()) as c:
        assert c.post("/threads", content=r'{"title": "a\ud800b"}', headers=hdr).status_code == 422
        assert c.put("/settings", content=r'{"model": "m\udc00"}', headers=hdr).status_code == 422
        tid = c.post("/threads", json={}).json()["id"]
        assert c.post(f"/threads/{tid}/messages",
                      content=r'{"message": "q\ud83d z"}', headers=hdr).status_code == 422


def test_oversize_inputs_rejected_422():
    """G3 크기 상한(무제한 입력 DoS 차단): title·settings·approved 길이/개수 초과는 422, 경계는 허용."""
    with _client(ApprovalAgent()) as c:
        # title 500 경계: 500 OK, 501 422
        assert c.post("/threads", json={"title": "t" * 500}).status_code == 200
        assert c.post("/threads", json={"title": "t" * 501}).status_code == 422
        # settings 값 2000 경계
        assert c.put("/settings", json={"server_url": "u" * 2000}).status_code == 200
        assert c.put("/settings", json={"model": "m" * 2001}).status_code == 422
        # approved 리스트 길이·원소 길이 상한
        tid = c.post("/threads", json={}).json()["id"]
        c.post(f"/threads/{tid}/messages", json={"message": "q"})
        _wait_status(c, tid, {"awaiting_approval"})
        assert c.post(f"/threads/{tid}/approve",
                      json={"approved": ["x"] * 101}).status_code == 422
        assert c.post(f"/threads/{tid}/approve",
                      json={"approved": ["x" * 201]}).status_code == 422
        # 정상 크기는 통과(awaiting 보존 — 위 422 는 처리 전 반려)
        assert _wait_status(c, tid, {"awaiting_approval"}) == "awaiting_approval"


def test_approve_nonuuid_thread_returns_404_not_500():
    """approve 도 다른 thread-스코프 라우트와 일관되게 비-UUID→404(get_active_run uuid 캐스트 500 방지)."""
    with _client(RunAgent()) as c:
        assert c.post("/threads/1 OR 1=1/approve", json={}).status_code == 404
        assert c.post("/threads/not-a-uuid/approve", json={}).status_code == 404


# ── G4: 내구 이벤트로그 — Last-Event-ID 재연결 + 교차 인스턴스 ────────────────
def _ids(resp):
    return [int(ln[len("id: "):]) for ln in resp.text.splitlines() if ln.startswith("id: ")]


def test_last_event_id_reconnect_replays_only_after_cursor():
    """Last-Event-ID(=seq) 재연결: 끊긴 seq 이후 이벤트만 replay(중복 없음), terminal 로 종료."""
    with _client(RunAgent()) as c:
        tid = c.post("/threads", json={}).json()["id"]
        run_id = c.post(f"/threads/{tid}/messages", json={"message": "q"}).json()["run_id"]
        _wait_status(c, tid, ("completed", "error"))
        full = c.get(f"/runs/{run_id}/stream")
        ids = _ids(full)
        assert _events(full)[-1] == "run.done" and len(ids) >= 3
        mid = ids[len(ids) // 2]
        resume = c.get(f"/runs/{run_id}/stream", headers={"Last-Event-ID": str(mid)})
        assert all(s > mid for s in _ids(resume))        # 커서 이후만(재전송 없음)
        assert "run.done" in _events(resume)             # 종료 이벤트 포함


def test_cross_instance_stream_reads_durable_log():
    """교차 인스턴스: B 인스턴스가 A 가 시작한 run 을 stream(인프로세스 버퍼엔 없지만 내구 로그로)."""
    appA = create_app(agent_factory=lambda cp: RunAgent())
    appB = create_app(agent_factory=lambda cp: RunAgent())
    with TestClient(appA) as ca, TestClient(appB) as cb:
        tid = ca.post("/threads", json={}).json()["id"]
        run_id = ca.post(f"/threads/{tid}/messages", json={"message": "q"}).json()["run_id"]
        _wait_status(ca, tid, ("completed", "error"))
        sB = cb.get(f"/runs/{run_id}/stream")            # B 는 이 run 을 시작한 적 없음
        names = _events(sB)
        assert names[-1] == "run.done" and "message.completed" in names


def test_second_instance_boot_does_not_kill_live_run():
    """G4 멀티워커(HIGH 수정): B 인스턴스 부팅 reconcile 이 A 의 살아있는(fresh heartbeat) running run 을
    죽이지 않는다. 수정 전엔 reconcile 이 모든 running 을 error 로 쓸어 상호 오살했음(실측 재현)."""
    agentA = GateAgent()
    with TestClient(create_app(agent_factory=lambda cp: agentA)) as ca:
        tid = ca.post("/threads", json={}).json()["id"]
        ca.post(f"/threads/{tid}/messages", json={"message": "q"})
        assert _wait_status(ca, tid, ("running",)) == "running"   # A 에서 running(gate 대기)
        # B 인스턴스 부팅+종료 — lifespan 이 reconcile_orphan_runs(grace) 실행
        with TestClient(create_app(agent_factory=lambda cp: RunAgent())):
            pass
        # A 의 run 이 살아남아야(heartbeat fresh) — 멀티워커 상호 오살 안 함
        with ca.app.state.repo.pool.connection() as conn:
            st = conn.execute("SELECT status::text FROM runs WHERE thread_id=%s", (tid,)).fetchone()[0]
        assert st == "running", f"B 부팅이 A 의 살아있는 run 을 죽임({st}) — 멀티워커 오살 회귀"
        agentA.gate.set()                                          # 정리
        _wait_status(ca, tid, ("completed", "error"))


# ── 런타임 모델 선택(GPT 다버전/로컬) + 사용 모델 기록 ────────────────────────
def _agent_with_model(mid, provider):
    import types as _t
    a = RunAgent()
    a.settings = _t.SimpleNamespace(llm_model=mid, provider=provider)
    return a


def _multi_registry():
    return {"gpt-5.4-nano": _agent_with_model("gpt-5.4-nano", "openai"),
            "qwen3.6-35b-a3b": _agent_with_model("qwen3.6-35b-a3b", "compatible")}


def test_models_lists_registry_with_one_default():
    with _client(_multi_registry()) as c:
        models = c.get("/models").json()["models"]
        assert {m["id"] for m in models} == {"gpt-5.4-nano", "qwen3.6-35b-a3b"}
        assert {m["provider"] for m in models} == {"openai", "compatible"}
        assert sum(1 for m in models if m["default"]) == 1          # 정확히 1개 기본


def test_selected_model_recorded_in_event_db_and_history():
    """런타임 선택 모델이 run.started 이벤트·runs·답변 메시지(이력)에 기록(문답별 사용 모델 추적)."""
    with _client(_multi_registry()) as c:
        tid = c.post("/threads", json={}).json()["id"]
        run_id = c.post(f"/threads/{tid}/messages",
                        json={"message": "거실?", "model": "gpt-5.4-nano"}).json()["run_id"]
        _wait_status(c, tid, ("completed", "error"))
        assert '"model": "gpt-5.4-nano"' in c.get(f"/runs/{run_id}/stream").text   # run.started
        with c.app.state.repo.pool.connection() as conn:
            rmodel = conn.execute("SELECT model FROM runs WHERE id=%s", (run_id,)).fetchone()[0]
        assert rmodel == "gpt-5.4-nano"                                            # runs.model
        agent_msg = next(m for m in c.get(f"/threads/{tid}/messages").json()["messages"]
                         if m["role"] == "agent")
        assert agent_msg["model"] == "gpt-5.4-nano"                               # messages.model(이력)


def test_unknown_model_rejected_422():
    with _client(_multi_registry()) as c:
        tid = c.post("/threads", json={}).json()["id"]
        r = c.post(f"/threads/{tid}/messages", json={"message": "q", "model": "gpt-9-imaginary"})
        assert r.status_code == 422


def test_default_model_used_when_unspecified():
    with _client(_multi_registry()) as c:
        tid = c.post("/threads", json={}).json()["id"]
        run_id = c.post(f"/threads/{tid}/messages", json={"message": "q"}).json()["run_id"]
        _wait_status(c, tid, ("completed", "error"))
        with c.app.state.repo.pool.connection() as conn:
            rmodel = conn.execute("SELECT model FROM runs WHERE id=%s", (run_id,)).fetchone()[0]
        assert rmodel == "gpt-5.4-nano"            # 미지정 → 기본(레지스트리 첫 항목)


def test_cross_instance_interrupt_running_no_double_terminal_or_orphan():
    """교차검증 Medium 수정: B 인스턴스가 A 의 running run 을 중지(best-effort)해도 A 의 좀비 pump 가
    double-terminal·orphan 완성답변을 남기지 않는다(finalize 외부종결 가드 + except CAS 가드)."""
    import threading as _th

    class GateAnswerAgent:
        def __init__(self): self.gate = _th.Event()
        def stream(self, message, thread_id="default"):
            yield _call_chunk()
            self.gate.wait(timeout=5)
            yield _tool_chunk(); yield _final_chunk()
        def resume(self, thread_id="default"): yield from ()
        def reject_pending(self, thread_id="default"): pass

    agentA = GateAnswerAgent()
    with TestClient(create_app(agent_factory=lambda cp: agentA)) as ca, \
         TestClient(create_app(agent_factory=lambda cp: RunAgent())) as cb:
        tid = ca.post("/threads", json={}).json()["id"]
        run_id = ca.post(f"/threads/{tid}/messages", json={"message": "q"}).json()["run_id"]
        _wait_status(ca, tid, ("running",))
        assert cb.post(f"/runs/{run_id}/interrupt").json()["status"] == "interrupted"   # 타 인스턴스
        agentA.gate.set()                                       # A pump 좀비 계속
        _wait_status(ca, tid, ("interrupted",))
        import time; time.sleep(0.25)                           # 잔여 이벤트 정착
        evs = ca.app.state.repo.get_run_events_after(run_id, -1)
        terms = [e for e in evs if e["event"] in ("run.done", "error")]
        assert len(terms) == 1 and terms[0]["data"].get("status") == "interrupted"   # double-terminal 0
        roles = [m["role"] for m in ca.get(f"/threads/{tid}/messages").json()["messages"]]
        assert "agent" not in roles                             # orphan 완성답변 0


def _run_model(c, run_id):
    with c.app.state.repo.pool.connection() as conn:
        return conn.execute("SELECT model FROM runs WHERE id=%s", (run_id,)).fetchone()[0]


def test_settings_model_becomes_run_default():
    """FE 가 PUT /settings 로 저장한 모델이 POST /messages(model 미지정)의 run 기본값으로 적용(실 전환)."""
    with _client(_multi_registry()) as c:
        c.put("/settings", json={"model": "qwen3.6-35b-a3b"})   # 기본은 gpt(첫 항목)인데 qwen 선택
        tid = c.post("/threads", json={}).json()["id"]
        rid = c.post(f"/threads/{tid}/messages", json={"message": "q"}).json()["run_id"]
        _wait_status(c, tid, ("completed", "error"))
        assert _run_model(c, rid) == "qwen3.6-35b-a3b"          # settings 선택 적용(기본 아님)


def test_body_model_overrides_settings_model():
    with _client(_multi_registry()) as c:
        c.put("/settings", json={"model": "qwen3.6-35b-a3b"})
        tid = c.post("/threads", json={}).json()["id"]
        rid = c.post(f"/threads/{tid}/messages",
                     json={"message": "q", "model": "gpt-5.4-nano"}).json()["run_id"]
        _wait_status(c, tid, ("completed", "error"))
        assert _run_model(c, rid) == "gpt-5.4-nano"             # 명시 body.model 이 우선


def test_stale_settings_model_falls_back_to_default_not_422():
    """저장된 모델이 레지스트리에 없으면(mock/구식) 422 아니라 백엔드 기본으로 graceful."""
    with _client(_multi_registry()) as c:
        c.put("/settings", json={"model": "qwen2.5-7b-mock"})   # 레지스트리에 없음
        tid = c.post("/threads", json={}).json()["id"]
        r = c.post(f"/threads/{tid}/messages", json={"message": "q"})
        assert r.status_code == 200                             # 422 아님
        _wait_status(c, tid, ("completed", "error"))
        assert _run_model(c, r.json()["run_id"]) == "gpt-5.4-nano"   # 기본(첫 항목)


def test_settings_partial_update_preserves_other_fields():
    """교차검증 HIGH 수정: model 만 PUT 해도 server_url/theme 보존(FE Do-16 흐름). 명시 null 은 비움."""
    with _client(RunAgent()) as c:
        c.put("/settings", json={"model": "m0", "server_url": "http://s0", "theme": "dark"})
        c.put("/settings", json={"model": "m1"})               # model 만 전송
        s = c.get("/settings").json()
        assert s["model"] == "m1" and s["server_url"] == "http://s0" and s["theme"] == "dark"
        c.put("/settings", json={"theme": None})               # 명시 null → theme 만 비움
        s2 = c.get("/settings").json()
        assert s2["theme"] is None and s2["model"] == "m1" and s2["server_url"] == "http://s0"


def test_settings_concurrent_partial_put_no_lost_update():
    """동시 부분 PUT(서로 다른 필드)이 서로를 lost-update 하지 않음(필드별 보존)."""
    import concurrent.futures as cf
    with _client(RunAgent()) as c:
        c.put("/settings", json={"model": "base", "server_url": "http://base", "theme": "base"})
        with cf.ThreadPoolExecutor(max_workers=2) as ex:
            list(ex.map(lambda kv: c.put("/settings", json=kv),
                        [{"model": "A"}, {"theme": "B"}]))
        s = c.get("/settings").json()
        assert s["model"] == "A" and s["theme"] == "B"         # 둘 다 반영
        assert s["server_url"] == "http://base"                # 미전송 필드 보존


def test_stream_db_terminal_reflects_status_not_always_error():
    """LOW 수정: run_events 에 terminal 없는데 DB 가 terminal 이면, stream 이 그 status 를 반영한
    run.done{status} 로 마무리(reject/interrupt 를 'error 비정상종료'로 오표시하던 것 차단)."""
    import asyncio
    from backend_app.api.run_manager import RunManager
    from backend_app.services.run_service import RunEvent  # noqa: F401

    class FakeRepo:
        def __init__(self, status): self.status = status
        def get_run_events_after(self, run_id, after_seq=-1, limit=1000):
            base = [{"seq": 1, "event": "run.started", "data": {"run_id": run_id}},
                    {"seq": 2, "event": "tool.call", "data": {}}]   # terminal 없음
            return [e for e in base if e["seq"] > after_seq]
        def get_run(self, run_id): return ("t1", self.status)

    class StubRS:
        def __init__(self, status): self.repo = FakeRepo(status)

    async def last_event(status):
        mgr = RunManager(StubRS(status))
        evs = [ev async for ev in mgr.stream("r1", -1)]
        mgr.shutdown()
        return evs[-1]

    for status, ev_name, ev_status in [("rejected", "run.done", "rejected"),
                                       ("interrupted", "run.done", "interrupted"),
                                       ("completed", "run.done", "completed"),
                                       ("error", "error", None)]:
        last = asyncio.run(asyncio.wait_for(last_event(status), timeout=5))
        assert last.event == ev_name, f"{status} → {last.event}"
        if ev_status:
            assert last.data.get("status") == ev_status
        else:
            assert "message" in last.data            # error 는 message 포함


def test_approval_requested_includes_pending_tools_e2e():
    """실 repo 경유: approval.requested 에 대기 도구(name·args) 포함 — FE 모달이 무엇을 승인하는지 표시.

    stream 은 awaiting_approval 에서 종료 안 하고 폴링(resume 대기)하므로 hang — 내구 로그서 직접 조회."""
    with _client(ApprovalAgent()) as c:
        tid = c.post("/threads", json={}).json()["id"]
        run_id = c.post(f"/threads/{tid}/messages", json={"message": "q"}).json()["run_id"]
        _wait_status(c, tid, ("awaiting_approval",))
        evs = c.app.state.repo.get_run_events_after(run_id, -1)   # run_events.data 는 JSONB→dict
        appr = next(e for e in evs if e["event"] == "approval.requested")
        tools = appr["data"]["tools"]
        assert appr["data"]["action"] == "tool" and isinstance(tools, list)
        assert tools[0]["name"] == "search_legal"
        assert tools[0]["args"] == {"query": "거실"}
        assert tools[0]["id"] == "c1"
