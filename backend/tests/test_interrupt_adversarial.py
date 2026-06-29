"""런타임 적대 검증 — interrupt(중지) Do-13 동시성·race·상태일관성.

소스 무수정. 실 PG(convstore@localhost:5434) + 스텁 agent(블로킹 gate). FastAPI TestClient.
각 가설 H1~H7 을 실제로 실행해 깨뜨린다. gap-detector 가 grep 만 한 코드를 진짜로 돌린다.

실행: PYTHONPATH="backend/src:agent:legal_core/src" agent/.venv/bin/python -m pytest <this> -v -s
"""
from __future__ import annotations

import threading
import time
import types
import uuid

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
_SETTINGS = types.SimpleNamespace(llm_model="qwen3.6-35b-a3b", provider="compatible")


@pytest.fixture(scope="module", autouse=True)
def _require_pg():
    try:
        p = build_pool(); p.close()
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"convstore postgres 미가동: {exc}")


# ── 메시지 스텁 ───────────────────────────────────────────────────────────────
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


def _client(agent):
    return TestClient(create_app(agent_factory=lambda cp: agent))


def _events(resp):
    return [ln[len("event: "):] for ln in resp.text.splitlines() if ln.startswith("event: ")]


def _wait_status(c, thread_id, statuses, timeout=8.0):
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
        time.sleep(0.01)
    raise AssertionError(f"run status {last} not in {statuses} within {timeout}s")


def _run_status(c, run_id):
    repo = c.app.state.repo
    with repo.pool.connection() as conn:
        row = conn.execute("SELECT status::text, ended_at FROM runs WHERE id=%s",
                           (run_id,)).fetchone()
    return row if row else None


# ── Agents ────────────────────────────────────────────────────────────────────
class GateAgent:
    """첫 청크를 gate 가 열릴 때까지 막는다(running 상태). gate 후 청크 N개를 천천히 방출."""
    def __init__(self, chunks=None, post_gate_sleep=0.0):
        self.gate = threading.Event()
        self.chunks = chunks or [_final_chunk()]
        self.post_gate_sleep = post_gate_sleep
        self.entered = threading.Event()
    def stream(self, message, thread_id="default"):
        self.entered.set()
        self.gate.wait(timeout=8)
        for ch in self.chunks:
            if self.post_gate_sleep:
                time.sleep(self.post_gate_sleep)
            yield ch
    def resume(self, thread_id="default"):
        yield from ()
    def reject_pending(self, thread_id="default"):
        pass
    settings = _SETTINGS


class ApprovalAgent:
    def stream(self, message, thread_id="default"):
        yield _call_chunk()
        yield {"__interrupt__": (_Interrupt("승인?"),)}
    def resume(self, thread_id="default"):
        yield _tool_chunk(); yield _final_chunk()
    def reject_pending(self, thread_id="default"):
        pass
    settings = _SETTINGS


class GatedResumeApprovalAgent(ApprovalAgent):
    """resume(승인) 시 gate 로 막아 running 상태를 길게 유지(approve↔interrupt race 관찰용)."""
    def __init__(self):
        self.resume_gate = threading.Event()
    def resume(self, thread_id="default"):
        self.resume_gate.wait(timeout=8)
        yield _tool_chunk(); yield _final_chunk()


# ══════════════════════════════════════════════════════════════════════════════
# H1: double-terminal — interrupt 직후 정상 완료 동시 발생 시 run.done 2개?
# ══════════════════════════════════════════════════════════════════════════════
def test_H1_double_terminal_race():
    """gate 가 열린 직후 _drive 가 청크를 소비하며 cancel 검사. interrupt 를 gate open 과
    거의 동시에 쳐서, _drive 의 (a)cancel CAS running→interrupted 와 (b)정상완료 CAS running→completed
    가 경합하게 만든다. CAS 가 단 하나만 통과해야 → 버퍼에 terminal(run.done/error)이 정확히 1개.
    여러 번 반복해 타이밍 윈도우를 훑는다."""
    results = []
    for trial in range(40):
        agent = GateAgent(chunks=[_final_chunk()])
        with _client(agent) as c:
            tid = c.post("/threads", json={}).json()["id"]
            run_id = c.post(f"/threads/{tid}/messages", json={"message": "q"}).json()["run_id"]
            agent.entered.wait(timeout=5)
            _wait_status(c, tid, ("running",))
            # interrupt 와 gate open 을 동시에 — _drive 가 청크 받기 직전/직후 cancel 경합
            barrier = threading.Barrier(2)
            def do_interrupt():
                barrier.wait()
                try:
                    c.post(f"/runs/{run_id}/interrupt")
                except Exception:
                    pass
            def do_gate():
                barrier.wait()
                agent.gate.set()
            t1 = threading.Thread(target=do_interrupt)
            t2 = threading.Thread(target=do_gate)
            t1.start(); t2.start(); t1.join(); t2.join()
            _wait_status(c, tid, ("interrupted", "completed", "error"))
            time.sleep(0.05)  # pump 가 마지막 terminal 푸시 완료 대기
            evs = _events(c.get(f"/runs/{run_id}/stream"))
            terminals = [e for e in evs if e in ("run.done", "error")]
            final_status = _run_status(c, run_id)[0]
            results.append((len(terminals), terminals, final_status, evs))
    # 판정: 모든 trial 에서 terminal 정확히 1개
    bad = [r for r in results if r[0] != 1]
    statuses = set(r[2] for r in results)
    print(f"\n[H1] trials={len(results)} statuses_seen={statuses} double_terminal_count={len(bad)}")
    if bad:
        for r in bad[:5]:
            print(f"  BAD: terminals={r[0]} {r[1]} status={r[2]} all={r[3]}")
    assert not bad, f"double-terminal: {len(bad)}/{len(results)} trials had !=1 terminal"


# ══════════════════════════════════════════════════════════════════════════════
# H2: interrupt↔approve race — awaiting_approval 에 동시 interrupt + approve
# ══════════════════════════════════════════════════════════════════════════════
def test_H2_interrupt_vs_approve_double_terminal_BUG():
    """awaiting_approval run 에 interrupt + approve(resume) 동시.

    **DB CAS 자체는 견고**: try_transition 으로 awaiting→interrupted 와 awaiting→running 중 단 1명만
    통과한다(이중 DB 전이 없음). final status 는 항상 interrupted XOR completed.

    **그러나 SSE 스트림 계층에 버그**: interrupt 가 CAS 를 이기면(awaiting→interrupted),
    동시 approve 는 RunManager.resume 가 get_active_run='awaiting_approval' 을 보고 통과해 **pump 를 이미
    스폰**한다. pump 내부의 RunService.resume 가 CAS awaiting→running 에 실패(False)→ValueError 를 던지고,
    pump 의 except 가 삼키나 saw_terminal=False·last_name=None 이라 finally 가 _synthetic_terminal()=error
    를 버퍼에 append → 같은 run 에 terminal 2개(run.done(interrupted)+error)가 들어가 stream 이 깨진다.

    이 테스트는 그 double-terminal 을 특성화한다(현 코드선 재현됨 → assert 로 버그 문서화)."""
    double_terminal_trials = 0
    db_anomaly_trials = 0      # DB 이중전이(진짜 치명) — 발생하면 안 됨
    trials = 60
    for trial in range(trials):
        agent = ApprovalAgent()
        with _client(agent) as c:
            tid = c.post("/threads", json={}).json()["id"]
            run_id = c.post(f"/threads/{tid}/messages", json={"message": "q"}).json()["run_id"]
            _wait_status(c, tid, ("awaiting_approval",))
            barrier = threading.Barrier(2)
            def do_interrupt():
                barrier.wait(); c.post(f"/runs/{run_id}/interrupt")
            def do_approve():
                barrier.wait(); c.post(f"/threads/{tid}/approve", json={"approve": True})
            threads = [threading.Thread(target=do_interrupt), threading.Thread(target=do_approve)]
            for t in threads: t.start()
            for t in threads: t.join()
            _wait_status(c, tid, ("interrupted", "completed", "error"), timeout=8)
            time.sleep(0.12)
            final = _run_status(c, run_id)[0]
            # G4: 인프로세스 버퍼 제거 → 내구 로그(run_events)에서 terminal 수를 센다.
            names = [e["event"] for e in c.app.state.repo.get_run_events_after(run_id, -1)]
            terms = [e for e in names if e in ("run.done", "error")]
            if len(terms) > 1:
                double_terminal_trials += 1
            # DB 이중전이 가드: final 은 항상 단일 터미널 상태여야(interrupted/completed/error)
            if final not in ("interrupted", "completed", "error"):
                db_anomaly_trials += 1
    print(f"\n[H2] trials={trials} double_terminal(SSE 버그)={double_terminal_trials} "
          f"db_double_transition(치명)={db_anomaly_trials}")
    # DB 계층은 견고해야(CAS) — 이게 깨지면 진짜 치명적 이중처리.
    assert db_anomaly_trials == 0, f"DB 이중전이(치명): {db_anomaly_trials}/{trials}"
    # **회귀 가드(수정 완료)**: _spawn_pump finally 가 이미 외부 종결(interrupted)된 run 에는 합성
    # terminal 을 억제 → 동시 interrupt↔approve 에서도 SSE terminal 은 항상 1개여야 한다(교차검증 HIGH 수정).
    assert double_terminal_trials == 0, (
        f"double-terminal 회귀: {double_terminal_trials}/{trials} trial 이 terminal 2개 방출 "
        "(_spawn_pump 합성 terminal 억제 가드가 깨졌나 확인)")


# ══════════════════════════════════════════════════════════════════════════════
# H3: interrupt↔completion race — 막 completed 전이 순간 interrupt → 정확히 409?
# ══════════════════════════════════════════════════════════════════════════════
def test_H3_interrupt_vs_completion_race():
    """running run 이 막 completed 로 가는 순간 interrupt. get_run 이 running 봤지만 request_cancel
    시점엔 _cancels pop 됨 + best-effort CAS running→interrupted 도 실패 → 409 정확해야.
    유령 terminal(completed 인데 run.done(interrupted) 추가 푸시)이 생기면 버그."""
    ghost = []
    for trial in range(40):
        # tool→final 로 합법 완주(cite frozen). gate 후 한 청크씩 흘려 완료 윈도우를 만든다.
        agent = GateAgent(chunks=[_tool_chunk(), _final_chunk()])
        with _client(agent) as c:
            tid = c.post("/threads", json={}).json()["id"]
            run_id = c.post(f"/threads/{tid}/messages", json={"message": "q"}).json()["run_id"]
            agent.entered.wait(timeout=5)
            _wait_status(c, tid, ("running",))
            # gate 를 열어 완료를 시작시키고, 아주 짧게 뒤따라 interrupt(완료 윈도우 경합)
            agent.gate.set()
            time.sleep(0.0005 * (trial % 5))  # 다양한 지연으로 윈도우 훑기
            r = c.post(f"/runs/{run_id}/interrupt")
            code = r.status_code
            js = r.json() if r.headers.get("content-type","").startswith("application/json") else {}
            _wait_status(c, tid, ("completed", "interrupted", "error"))
            time.sleep(0.05)
            final = _run_status(c, run_id)[0]
            evs = _events(c.get(f"/runs/{run_id}/stream"))
            terminals = [e for e in evs if e in ("run.done","error")]
            # 유령: final=completed 인데 interrupt 가 200+terminal 추가 / terminal 2개
            interrupt_status = js.get("status")
            anomaly = None
            if len(terminals) != 1:
                anomaly = f"terminals={len(terminals)} {terminals}"
            elif final == "completed" and code == 200 and interrupt_status == "interrupted":
                anomaly = f"interrupt claimed interrupted but DB=completed (code={code})"
            elif final == "completed" and code not in (200, 409):
                # 200 가능: cancel 이 완료 직전 통과해 실제 interrupted 됐을 수도(그 경우 final=interrupted)
                anomaly = f"unexpected code={code} for completed run"
            if anomaly:
                ghost.append((trial, code, interrupt_status, final, terminals, anomaly))
    print(f"\n[H3] trials=40 anomalies={len(ghost)}")
    for g in ghost[:8]:
        print(f"  trial={g[0]} code={g[1]} claim={g[2]} db={g[3]} terms={g[4]} :: {g[5]}")
    assert not ghost, f"interrupt/completion race 이상: {len(ghost)}/40"


# ══════════════════════════════════════════════════════════════════════════════
# H4: cancel 플래그 누수 — _drive finally pop 후 _cancels 잔여?
# ══════════════════════════════════════════════════════════════════════════════
def test_H4_cancel_flag_leak():
    """경로별로 run 후 RunService._cancels 에 잔여 키가 남는지 직접 관찰.
    (a)정상완료 (b)error (c)awaiting 일시정지→resume→완료 (d)interrupt 후."""
    leaks = {}

    # (a) 정상완료
    class CompleteAgent:
        def stream(self, m, thread_id="default"):
            yield _call_chunk(); yield _tool_chunk(); yield _final_chunk()
        def resume(self, thread_id="default"): yield from ()
        def reject_pending(self, thread_id="default"): pass
        settings = _SETTINGS
    with _client(CompleteAgent()) as c:
        rs = c.app.state.run_service
        tid = c.post("/threads", json={}).json()["id"]
        rid = c.post(f"/threads/{tid}/messages", json={"message":"q"}).json()["run_id"]
        _wait_status(c, tid, ("completed",)); time.sleep(0.1)
        leaks["complete"] = (rid in rs._cancels, dict(rs._cancels))

    # (b) error (agent 예외)
    class ErrorAgent:
        def stream(self, m, thread_id="default"):
            yield _call_chunk()
            raise RuntimeError("boom")
        def resume(self, thread_id="default"): yield from ()
        def reject_pending(self, thread_id="default"): pass
        settings = _SETTINGS
    with _client(ErrorAgent()) as c:
        rs = c.app.state.run_service
        tid = c.post("/threads", json={}).json()["id"]
        rid = c.post(f"/threads/{tid}/messages", json={"message":"q"}).json()["run_id"]
        _wait_status(c, tid, ("error",)); time.sleep(0.1)
        leaks["error"] = (rid in rs._cancels, dict(rs._cancels))

    # (c) awaiting 일시정지 → resume → 완료
    with _client(ApprovalAgent()) as c:
        rs = c.app.state.run_service
        tid = c.post("/threads", json={}).json()["id"]
        rid = c.post(f"/threads/{tid}/messages", json={"message":"q"}).json()["run_id"]
        _wait_status(c, tid, ("awaiting_approval",))
        # 일시정지 시점: _drive finally 가 _cancels pop 했는지?
        paused_leak = rid in rs._cancels
        c.post(f"/threads/{tid}/approve", json={"approve": True})
        _wait_status(c, tid, ("completed",)); time.sleep(0.1)
        leaks["paused_then_resume"] = (rid in rs._cancels, dict(rs._cancels), f"paused_leak={paused_leak}")

    # (d) interrupt 후
    agent = GateAgent(chunks=[_final_chunk()])
    with _client(agent) as c:
        rs = c.app.state.run_service
        tid = c.post("/threads", json={}).json()["id"]
        rid = c.post(f"/threads/{tid}/messages", json={"message":"q"}).json()["run_id"]
        agent.entered.wait(5); _wait_status(c, tid, ("running",))
        c.post(f"/runs/{rid}/interrupt")
        agent.gate.set()
        _wait_status(c, tid, ("interrupted",)); time.sleep(0.1)
        leaks["interrupt"] = (rid in rs._cancels, dict(rs._cancels))

    print("\n[H4] cancel flag leak per path:")
    bad = []
    for path, info in leaks.items():
        leaked = info[0]
        print(f"  {path}: leaked={leaked} remaining_keys={list(info[1].keys())} extra={info[2:]}")
        if leaked:
            bad.append(path)
    assert not bad, f"_cancels 누수 경로: {bad}"


def test_H4b_resume_stale_flag_contamination():
    """resume 이 setdefault 로 재생성한 플래그를 같은 run 의 이전 interrupt(이미 pop)가 오염시키나?
    awaiting→interrupt 시도(interrupt_paused, _cancels 무관)→그래도 resume 가능?(아니어야: interrupted 는 종료)
    그리고 정상 일시정지→resume 시 새 플래그가 set 안된 상태인지 확인."""
    with _client(ApprovalAgent()) as c:
        rs = c.app.state.run_service
        tid = c.post("/threads", json={}).json()["id"]
        rid = c.post(f"/threads/{tid}/messages", json={"message":"q"}).json()["run_id"]
        _wait_status(c, tid, ("awaiting_approval",))
        # 정상 resume 후 _cancels[rid] 가 존재하면 set 되지 않은 깨끗한 플래그여야
        c.post(f"/threads/{tid}/approve", json={"approve": True})
        # resume 도중 잠깐 잡아 플래그 상태 관찰(완료 전)
        observed = []
        for _ in range(50):
            ev = rs._cancels.get(rid)
            if ev is not None:
                observed.append(ev.is_set())
            time.sleep(0.005)
        _wait_status(c, tid, ("completed",))
        print(f"\n[H4b] resume 중 _cancels[rid].is_set() 관찰값: {set(observed)}")
        assert True not in observed, "resume 의 새 플래그가 stale set 으로 오염됨"


# ══════════════════════════════════════════════════════════════════════════════
# H5: 마이그0003 정확성 — interrupted 가 활성 인덱스서 제외? 멱등? get_active_run?
# ══════════════════════════════════════════════════════════════════════════════
def test_H5_migration_index_semantics():
    repo_holder = {}
    with _client(GateAgent()) as c:
        repo = c.app.state.repo
        pool = repo.pool
        repo_holder["pool"] = pool

        # (1) 멱등 재실행: 마이그 0003 두 번 더 적용해도 에러 없어야
        from backend_app.db import run_migrations
        applied1 = run_migrations(pool)
        applied2 = run_migrations(pool)
        print(f"\n[H5] 마이그 재실행 OK (idempotent): {applied2}")

        # (2) interrupted run 있는 thread 에 새 run INSERT 가 409 없이 성공해야
        tid = repo.create_thread(title="h5")
        rid1 = repo.open_run(tid)  # running
        ok = repo.try_transition(rid1, "running", "interrupted", ended=True)
        assert ok, "running→interrupted 전이 실패"
        # 같은 thread 에 새 run — interrupted 가 활성 제외라면 성공
        try:
            rid2 = repo.open_run(tid)
            new_run_ok = True
        except Exception as e:  # noqa: BLE001
            new_run_ok = False
            print(f"  새 run INSERT 실패(버그 가능): {e}")
        print(f"  interrupted 후 새 run INSERT 성공={new_run_ok}")
        assert new_run_ok, "interrupted 가 활성 인덱스서 제외 안됨 → thread 영구 잠금"

        # (3) running 1개 제약 유지: 같은 thread 에 두번째 running 은 409
        dup_blocked = False
        try:
            repo.open_run(tid)  # 이미 rid2 running
        except Exception:
            dup_blocked = True
        print(f"  중복 running 차단(409)={dup_blocked}")
        assert dup_blocked, "running 동시 제약 깨짐"

        # (4) awaiting_approval + running 동시 불가 검증
        repo.try_transition(rid2, "running", "awaiting_approval")
        running_with_awaiting_blocked = False
        try:
            repo.open_run(tid)  # awaiting 있는데 running INSERT
        except Exception:
            running_with_awaiting_blocked = True
        print(f"  awaiting+running 동시 차단={running_with_awaiting_blocked}")
        assert running_with_awaiting_blocked, "awaiting_approval+running 동시 가능 — 인덱스 결함"

        # (5) get_active_run 이 interrupted 를 활성으로 안 봄
        tid2 = repo.create_thread(title="h5b")
        rid3 = repo.open_run(tid2)
        repo.try_transition(rid3, "running", "interrupted", ended=True)
        active = repo.get_active_run(tid2)
        print(f"  get_active_run(interrupted only)={active}")
        assert active is None, "get_active_run 이 interrupted 를 활성으로 봄"

        # (6) 실제 인덱스 술어 확인
        with pool.connection() as conn:
            idxdef = conn.execute(
                "SELECT indexdef FROM pg_indexes WHERE indexname='one_active_run_per_thread'"
            ).fetchone()
        print(f"  index def: {idxdef[0] if idxdef else None}")
        assert idxdef and "interrupted" not in idxdef[0], "인덱스 술어에 interrupted 포함됨"
        assert "running" in idxdef[0] and "awaiting_approval" in idxdef[0]


# ══════════════════════════════════════════════════════════════════════════════
# H6: interrupt 후 상태/스트림 일관성
# ══════════════════════════════════════════════════════════════════════════════
def test_H6_post_interrupt_consistency():
    # running interrupt
    agent = GateAgent(chunks=[_final_chunk()])
    with _client(agent) as c:
        tid = c.post("/threads", json={}).json()["id"]
        rid = c.post(f"/threads/{tid}/messages", json={"message":"q"}).json()["run_id"]
        agent.entered.wait(5); _wait_status(c, tid, ("running",))
        c.post(f"/runs/{rid}/interrupt")
        agent.gate.set()
        _wait_status(c, tid, ("interrupted",))
        st, ended = _run_status(c, rid)
        print(f"\n[H6-running] status={st} ended_at_set={ended is not None}")
        assert st == "interrupted" and ended is not None, "DB status/ended_at 불일치"
        # stream 이 hang 없이 run.done 으로 종료
        import asyncio
        async def drain():
            evs = [ev.event async for ev in c.app.state.runs.stream(rid)]
            return evs
        evs = asyncio.run(asyncio.wait_for(drain(), timeout=5))
        print(f"  stream events={evs}")
        assert evs[-1] in ("run.done","error"), "stream terminal 종료 안됨"
        # transcript 정합(중지 turn 의 부분 메시지)
        msgs = c.get(f"/threads/{tid}/messages").json()["messages"]
        roles = [m["role"] for m in msgs]
        print(f"  transcript roles={roles}")
        assert "user" in roles, "user 메시지 누락"

    # awaiting interrupt — 이미 stream 중인 클라가 terminal 받나
    with _client(ApprovalAgent()) as c:
        tid = c.post("/threads", json={}).json()["id"]
        rid = c.post(f"/threads/{tid}/messages", json={"message":"q"}).json()["run_id"]
        _wait_status(c, tid, ("awaiting_approval",))
        c.post(f"/runs/{rid}/interrupt")
        _wait_status(c, tid, ("interrupted",))
        st, ended = _run_status(c, rid)
        print(f"[H6-awaiting] status={st} ended_at_set={ended is not None}")
        assert st == "interrupted" and ended is not None
        import asyncio
        async def drain2():
            return [ev.event async for ev in c.app.state.runs.stream(rid)]
        evs = asyncio.run(asyncio.wait_for(drain2(), timeout=5))
        print(f"  stream events={evs}")
        assert evs[-1] == "run.done"
        # 새 run 시작 가능
        assert c.post(f"/threads/{tid}/messages", json={"message":"다시"}).status_code == 200


# ══════════════════════════════════════════════════════════════════════════════
# H7: 고아/없는 run interrupt
# ══════════════════════════════════════════════════════════════════════════════
def test_H7_orphan_and_missing_interrupt():
    with _client(GateAgent()) as c:
        repo = c.app.state.repo
        runs_mgr = c.app.state.runs

        # 없는 run → 404
        assert c.post(f"/runs/{uuid.uuid4()}/interrupt").status_code == 404
        # 비-UUID → 422
        assert c.post("/runs/not-a-uuid/interrupt").status_code == 422

        # 고아 running(버퍼/_cancels 에 없지만 DB=running) — best-effort CAS + _push_terminal
        tid = repo.create_thread(title="h7")
        orphan_rid = repo.open_run(tid)  # DB=running, RunManager 버퍼/_cancels 무관
        in_cancels = orphan_rid in c.app.state.run_service._cancels
        print(f"\n[H7] orphan in _cancels={in_cancels}")
        r = c.post(f"/runs/{orphan_rid}/interrupt")
        print(f"  orphan interrupt code={r.status_code} body={r.json() if r.status_code==200 else r.text[:80]}")
        assert r.status_code == 200, "고아 running interrupt 가 best-effort CAS 로 종결 못함"
        assert r.json()["status"] == "interrupted"
        st, ended = _run_status(c, orphan_rid)
        assert st == "interrupted" and ended is not None
        # G4: _push_terminal 이 **내구 로그**에 run.done(interrupted) 영속(버퍼 아님)
        evs = repo.get_run_events_after(orphan_rid, -1)
        assert evs and evs[-1]["event"] == "run.done" and evs[-1]["data"].get("status") == "interrupted"
        print(f"  orphan log last={evs[-1]['event']} status={evs[-1]['data'].get('status')}")

        # 이미 interrupted 재-interrupt → 409
        r2 = c.post(f"/runs/{orphan_rid}/interrupt")
        print(f"  re-interrupt code={r2.status_code}")
        assert r2.status_code == 409

        # thread 잠금 해제 확인(새 run 가능)
        assert repo.get_active_run(tid) is None
