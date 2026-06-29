"""런타임 적대 검증 — _finalize 외부종결 가드 + _drive except error-emit CAS 가드.

**적대 대상(소스 무수정, 보고만)**:
  run_service._finalize 진입:  cur = get_run(run_id); if cur is None or cur[1] != "running": return False
  run_service._drive except:   if try_transition(run_id,"running","error",ended=True): yield error

반증할 가설(G1~G7):
  G1 ★ finalize 윈도우 race: get_run(running) 직후·add_message 직전 interrupt → orphan 재발?
  G2 ★ 정상경로 false skip: 정상 완료/멀티턴/고동시성서 정당 답변이 스킵되나?
  G3 except CAS 가드가 진짜 error 를 삼키나(외부 선전이 시 stream 종료성)?
  G4 cite_forgery 와 상호작용(정상 running 위조 → 정당 error)?
  G5 resume/awaiting/reject 경로 영향?
  G6 per-run get_run 비용(고동시성 풀 경합)?
  G7 stranded 중간 이벤트 재연결 노출(benign?)

실 PG + 페이크 repo/agent 혼용. G1·G3 은 repo 메서드 monkeypatch 로 좁은 윈도우를 결정론적으로 연다
(소스 무수정 — 테스트가 repo 인스턴스의 바운드 메서드만 래핑).

실행: PYTHONPATH="backend/src:agent:legal_core/src" agent/.venv/bin/python -m pytest <this> -v -s
"""
from __future__ import annotations

import threading
import time
import types

import pytest

pytest.importorskip("psycopg")
pytest.importorskip("fastapi")

from fastapi.testclient import TestClient  # noqa: E402

from legal_core import ids  # noqa: E402
from legal_core.schemas import AnswerContext, LawRef  # noqa: E402

from backend_app.api import create_app  # noqa: E402
from backend_app.db import build_pool  # noqa: E402
from backend_app.repositories import ConversationRepository  # noqa: E402
from backend_app.services.run_service import RunService  # noqa: E402

_URI = ids.article_iri("099003", "20260227", 2)
_CID = ids.point_id(_URI)
_SETTINGS = types.SimpleNamespace(llm_model="qwen3.6-35b-a3b", provider="compatible")


@pytest.fixture(scope="module", autouse=True)
def _require_pg():
    try:
        p = build_pool(); p.close()
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"convstore postgres 미가동: {exc}")


@pytest.fixture(scope="module")
def pool():
    p = build_pool()
    from backend_app.db import run_migrations
    run_migrations(p)
    yield p
    p.close()


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


def _final_chunk(cid=_CID):
    return {"agent": {"messages": [AIMsg(content=f"건축법상 거실은 ...이다 [[cite:{cid}]].")]}}


def _make_thread(repo):
    return repo.create_thread(title="g")


def _msgs(repo, thread_id):
    return repo.get_thread_messages(thread_id)


def _agent_msgs(repo, thread_id):
    return [m for m in _msgs(repo, thread_id) if m["role"] == "agent"]


# ════════════════════════════════════════════════════════════════════════════
# G1 ★ finalize 윈도우 race — get_run(running) 직후·add_message 직전 interrupt
# ════════════════════════════════════════════════════════════════════════════
def test_G1_finalize_window_interrupt_no_orphan(pool):
    """_finalize 의 get_run 이 'running' 을 본 **직후** 외부가 run 을 interrupted 로 전이시키면?

    좁은 윈도우를 결정론적으로 열기 위해 repo.get_run 을 래핑: _finalize 호출(run_id 인자)에서
    'running' 을 돌려준 직후 DB 를 직접 interrupted 로 전이(외부 종결 시뮬). 그 뒤 _finalize 는
    이미 'running' 으로 통과했으므로 add_message(orphan 답변)·message.completed 를 진행한다.

    검증할 증상:
      (1) orphan: interrupted run 인데 agent 답변(message.completed)이 영속되나?
      (2) 마지막 try_transition(running→completed) 은 패배(이미 interrupted) → run.done 억제되나?
      (3) DB 최종상태는 interrupted 로 유지되나(부활 없음)?
    """
    repo = ConversationRepository(pool)
    thread_id = _make_thread(repo)

    class FinalOnly:
        def stream(self, message, thread_id="default"):
            yield _tool_chunk()
            yield _final_chunk()

    rs = RunService(FinalOnly(), repo)

    # **수정 후**: _finalize 는 진입 get_run 가드 대신 commit_agent_answer(CAS+INSERT 한 tx)로 영속.
    # 그 commit 의 CAS **직전**에 외부 interrupt 를 끼워도 CAS(running→completed) 패배→전체 롤백→orphan 0.
    real_commit = repo.commit_agent_answer
    real_tt = repo.try_transition
    flipped = {"done": False}

    def racing_commit(thread_id_, run_id, **kw):
        if not flipped["done"]:
            flipped["done"] = True
            assert real_tt(run_id, "running", "interrupted", ended=True)  # commit 직전 외부 종결
        return real_commit(thread_id_, run_id, **kw)

    repo.commit_agent_answer = racing_commit
    try:
        evs = list(rs.run("q", thread_id=thread_id))
    finally:
        repo.commit_agent_answer = real_commit

    names = [e.event for e in evs]
    with pool.connection() as conn:
        st = conn.execute("SELECT status::text FROM runs WHERE thread_id=%s",
                          (thread_id,)).fetchone()[0]
    agent_msgs = _agent_msgs(repo, thread_id)
    print(f"\n[G1] events={names} db_status={st} agent_msgs={len(agent_msgs)}")
    assert flipped["done"], "commit 직전 침입 시뮬이 트리거 안 됨"
    assert st == "interrupted", f"외부종결 run 이 부활됨: db={st}"      # 부활 차단(견고)
    assert "message.completed" not in names, "orphan 답변 방출(원자 CAS 가 막아야)"
    assert "run.done" not in names
    assert len(agent_msgs) == 0, "interrupted run 에 orphan 답변 영속(원자 commit 롤백 실패)"


def test_G1b_finalize_window_concurrent_trials(pool):
    """monkeypatch 없는 실제 동시성으로 같은 윈도우를 다수 trial 노출 시도(자연 발생 여부).

    gate 로 막은 agent 가 마지막 청크를 흘리는 순간(=_finalize 진입 근처) interrupt 를 친다.
    barrier 로 gate-open 과 interrupt 를 동시 발사. orphan(interrupted + agent 답변)이
    한 번이라도 나오면 윈도우가 자연 노출 가능(심각). DB CAS 견고성도 같이 본다."""
    orphans = []
    trials = 50
    for t in range(trials):
        agent = GateAgent(chunks=[_tool_chunk(), _final_chunk()])
        with _client(agent) as c:
            tid = c.post("/threads", json={}).json()["id"]
            rid = c.post(f"/threads/{tid}/messages", json={"message": "q"}).json()["run_id"]
            agent.entered.wait(timeout=5)
            _wait_status(c, tid, ("running",))
            barrier = threading.Barrier(2)

            def do_gate():
                barrier.wait(); agent.gate.set()

            def do_intr():
                barrier.wait(); time.sleep(0.0003 * (t % 4))
                try:
                    c.post(f"/runs/{rid}/interrupt")
                except Exception:  # noqa: BLE001
                    pass

            th = [threading.Thread(target=do_gate), threading.Thread(target=do_intr)]
            [x.start() for x in th]; [x.join() for x in th]
            _wait_status(c, tid, ("interrupted", "completed", "error"))
            time.sleep(0.05)
            final = _run_status(c, rid)[0]
            names = [e["event"] for e in c.app.state.repo.get_run_events_after(rid, -1)]
            agent_msgs = [m for m in c.get(f"/threads/{tid}/messages").json()["messages"]
                          if m["role"] == "agent"]
            terms = [e for e in names if e in ("run.done", "error")]
            # orphan = interrupted 인데 answer 가 영속/방출됨
            if final == "interrupted" and (agent_msgs or "message.completed" in names):
                orphans.append((t, final, names, len(agent_msgs)))
            # double-terminal 도 같이 체크
            if len(terms) > 1:
                orphans.append((t, f"{final}/DOUBLE-TERM", names, len(agent_msgs)))
    print(f"\n[G1b] trials={trials} orphan_or_anomaly={len(orphans)}")
    for o in orphans[:8]:
        print(f"  trial={o[0]} final={o[1]} agent_msgs={o[3]} events={o[2]}")
    assert not orphans, f"finalize 윈도우 자연 노출/이중종결: {len(orphans)}/{trials}"


# ════════════════════════════════════════════════════════════════════════════
# G2 ★ 정상경로 회귀(false skip) — 정상 완료/멀티턴/고동시성서 답변 영속
# ════════════════════════════════════════════════════════════════════════════
def test_G2_normal_path_persists_answer(pool):
    """외부 interrupt 없는 정상 완료 run 이 finalize get_run 에서 running 을 보고 답변을 영속한다."""
    repo = ConversationRepository(pool)
    tid = _make_thread(repo)

    class Normal:
        def stream(self, m, thread_id="default"):
            yield _tool_chunk(); yield _final_chunk()

    evs = list(RunService(Normal(), repo).run("q", thread_id=tid))
    names = [e.event for e in evs]
    print(f"\n[G2] events={names}")
    assert "message.completed" in names, "정상 run 의 답변이 false-skip 됨"
    assert names[-1] == "run.done"
    assert len(_agent_msgs(repo, tid)) == 1


def test_G2b_multiturn_same_thread_each_answer_persists(pool):
    """같은 thread 멀티턴 — 각 턴 답변이 모두 영속(get_run 이 직전 턴 stale 을 안 읽음)."""
    repo = ConversationRepository(pool)
    tid = _make_thread(repo)

    class Normal:
        def stream(self, m, thread_id="default"):
            yield _tool_chunk(); yield _final_chunk()

    for turn in range(4):
        evs = list(RunService(Normal(), repo).run(f"q{turn}", thread_id=tid))
        names = [e.event for e in evs]
        assert "message.completed" in names and names[-1] == "run.done", \
            f"turn {turn} 답변 false-skip: {names}"
    agent_msgs = _agent_msgs(repo, tid)
    print(f"\n[G2b] turns=4 agent_msgs={len(agent_msgs)}")
    assert len(agent_msgs) == 4, f"멀티턴 답변 영속 누락: {len(agent_msgs)}/4"


def test_G2c_high_concurrency_no_false_skip(pool):
    """고동시성: 다수 thread 가 동시에 finalize. 각 run 의 get_run 이 **자기** run_id 로 조회하므로
    엉뚱한 run·stale 을 읽어 정당 답변을 스킵하면 안 된다. N thread × 동시 run → 전부 답변 영속."""
    repo = ConversationRepository(pool)
    N = 40
    tids = [_make_thread(repo) for _ in range(N)]
    results = {}
    barrier = threading.Barrier(N)

    class Normal:
        def stream(self, m, thread_id="default"):
            yield _tool_chunk(); yield _final_chunk()

    def run_one(tid):
        barrier.wait()
        evs = list(RunService(Normal(), repo).run("q", thread_id=tid))
        results[tid] = [e.event for e in evs]

    ths = [threading.Thread(target=run_one, args=(tid,)) for tid in tids]
    [t.start() for t in ths]; [t.join() for t in ths]

    skipped = [tid for tid in tids if "message.completed" not in results[tid]]
    no_done = [tid for tid in tids if "run.done" not in results[tid]]
    persisted = sum(1 for tid in tids if len(_agent_msgs(repo, tid)) == 1)
    print(f"\n[G2c] N={N} false_skip={len(skipped)} no_run_done={len(no_done)} persisted={persisted}/{N}")
    assert not skipped, f"고동시성 false-skip(message.completed 누락): {len(skipped)}/{N}"
    assert not no_done, f"고동시성 run.done 누락: {len(no_done)}/{N}"
    assert persisted == N, f"답변 영속 누락: {persisted}/{N}"


# ════════════════════════════════════════════════════════════════════════════
# G3 except CAS 가드 — 진짜 예외 시 error 방출·삼킴·stream 종료성
# ════════════════════════════════════════════════════════════════════════════
def test_G3_genuine_exception_emits_error(pool):
    """agent 가 진짜 예외(외부 interrupt 아님)를 던지고 run 이 아직 running → CAS 성공 → error 방출."""
    repo = ConversationRepository(pool)
    tid = _make_thread(repo)

    class Boom:
        def stream(self, m, thread_id="default"):
            yield _tool_chunk()
            raise RuntimeError("boom")

    evs = list(RunService(Boom(), repo).run("q", thread_id=tid))
    names = [e.event for e in evs]
    with pool.connection() as conn:
        st = conn.execute("SELECT status::text FROM runs WHERE thread_id=%s",
                          (tid,)).fetchone()[0]
    print(f"\n[G3] events={names} db_status={st}")
    assert names[-1] == "error", "정당 예외가 error 로 방출 안 됨"
    assert st == "error"


def test_G3b_exception_after_external_error_swallows_error_but_drive_terminates(pool):
    """except CAS 가 진짜 error 를 삼키는 경우: 예외 발생 사이 sweep 이 먼저 error 로 전이.

    그러면 _drive except 의 try_transition(running→error) 은 패배(이미 error)→error **미방출**.
    _drive 제너레이터는 그래도 정상 종료(yield 없이 return)되나? → pump 가 terminal 없음을 보고
    합성 terminal 로 stream 종료시키나(hang 0)? RunManager 경로로 종단검증.

    즉 '정당 error 가 사용자에게 안 보이는 회귀' 가 stream hang 으로 이어지지 않는지(benign 인지)."""
    repo = ConversationRepository(pool)
    tid = _make_thread(repo)

    class Boom:
        def __init__(self):
            self.entered = threading.Event()
            self.gate = threading.Event()

        def stream(self, m, thread_id="default"):
            yield _tool_chunk()
            self.entered.set()
            self.gate.wait(timeout=5)   # 외부 sweep 이 error 로 먼저 전이할 시간
            raise RuntimeError("boom")

    agent = Boom()
    rs = RunService(agent, repo)
    gen = rs.run("q", thread_id=tid)
    # 백그라운드로 소비
    collected = []
    done = threading.Event()

    def drive():
        try:
            for ev in gen:
                collected.append(ev.event)
        finally:
            done.set()

    th = threading.Thread(target=drive); th.start()
    assert agent.entered.wait(5)
    # 외부 sweep 이 running→error 로 먼저 전이(예외 직전)
    run_id = None
    with pool.connection() as conn:
        run_id = conn.execute("SELECT id::text FROM runs WHERE thread_id=%s", (tid,)).fetchone()[0]
    assert repo.try_transition(run_id, "running", "error", ended=True), "외부 sweep 전이 실패"
    agent.gate.set()
    done.wait(5)
    th.join(5)

    names = collected
    with pool.connection() as conn:
        st = conn.execute("SELECT status::text FROM runs WHERE id=%s", (run_id,)).fetchone()[0]
    print(f"\n[G3b] _drive events={names} db_status={st}")
    # except CAS 패배 → error 미방출(삼킴). _drive 는 yield 없이 정상 return 해야(hang 아님).
    assert "error" not in names, "이미 외부 error 인데 _drive 가 또 error 방출(이중종결)"
    assert done.is_set(), "_drive 가 종료 안 됨(hang)"
    assert st == "error"
    print("  → _drive 는 error 삼키고 무방출 종료. stream 종료는 pump 합성 terminal 책임(G3c 에서 검증).")


def test_G3c_swallowed_error_stream_synthesizes_terminal(pool):
    """G3b 의 '삼켜진 error' 가 RunManager 경로에서 합성 terminal 로 stream 종료되나(hang 0)?

    pump 가 terminal 없이 끝나고 DB=error(terminal status)면 stream 의 poll 루프가 합성 종료한다."""
    import asyncio

    class Boom:
        def __init__(self):
            self.entered = threading.Event()
            self.gate = threading.Event()

        def stream(self, m, thread_id="default"):
            yield _tool_chunk()
            self.entered.set()
            self.gate.wait(timeout=5)
            raise RuntimeError("boom")

        def resume(self, thread_id="default"):
            yield from ()

        def reject_pending(self, thread_id="default"):
            pass

        settings = _SETTINGS

    agent = Boom()
    with _client(agent) as c:
        tid = c.post("/threads", json={}).json()["id"]
        rid = c.post(f"/threads/{tid}/messages", json={"message": "q"}).json()["run_id"]
        agent.entered.wait(5)
        # 외부 sweep 이 먼저 error 로 전이 → pump 의 except CAS 는 패배
        assert c.app.state.repo.try_transition(rid, "running", "error", ended=True)
        agent.gate.set()
        _wait_status(c, tid, ("error",))
        time.sleep(0.1)

        async def drain():
            return [ev.event async for ev in c.app.state.runs.stream(rid)]

        evs = asyncio.run(asyncio.wait_for(drain(), timeout=6))
        names = [e["event"] for e in c.app.state.repo.get_run_events_after(rid, -1)]
        print(f"\n[G3c] stream_events={evs} durable_log={names}")
        assert evs and evs[-1] in ("run.done", "error"), "stream 이 terminal 종료 안 됨(hang)"
        # 정당 error 가 '사용자에게 보이긴 함'(합성 terminal=error). 단 durable 로그엔 안 남을 수 있음.
        terms = [e for e in names if e in ("run.done", "error")]
        print(f"  durable terminals={terms} (합성은 비영속이라 0~1)")


# ════════════════════════════════════════════════════════════════════════════
# G4 cite_forgery 와 상호작용
# ════════════════════════════════════════════════════════════════════════════
def test_G4_normal_running_forgery_still_errors(pool):
    """정상 running 인데 본문이 위조 cite → 가드가 running 확인 후 진행 → cite_forgery error 정상 방출."""
    repo = ConversationRepository(pool)
    tid = _make_thread(repo)

    class Forge:
        def stream(self, m, thread_id="default"):
            yield _tool_chunk()
            yield {"agent": {"messages": [AIMsg(content="근거없는단언 [[cite:NOPE]].")]}}

    evs = list(RunService(Forge(), repo).run("q", thread_id=tid))
    names = [e.event for e in evs]
    err = next((e for e in evs if e.event == "error"), None)
    print(f"\n[G4] events={names} forged={err.data.get('forged') if err else None}")
    assert err is not None and err.data.get("reason") == "cite_forgery", \
        "정상 running 위조가 cite_forgery error 로 안 막힘(가드가 정당 검증을 삼킴)"
    assert "message.completed" not in names
    assert not _agent_msgs(repo, tid), "위조인데 답변이 영속됨"


def test_G4b_external_termination_plus_forgery_skips_both(pool):
    """외부종결 + 위조 겹침: get_run 이 interrupted 를 보면 위조검증도 안 타고 전부 스킵(orphan/error 둘 다 없음)."""
    repo = ConversationRepository(pool)
    tid = _make_thread(repo)

    class Forge:
        def stream(self, m, thread_id="default"):
            yield _tool_chunk()
            yield {"agent": {"messages": [AIMsg(content="근거없는단언 [[cite:NOPE]].")]}}

    rs = RunService(Forge(), repo)
    real_tt = repo.try_transition
    flipped = {"done": False}

    def racing_tt(run_id, frm, to, **kw):
        # **수정 후**: forgery 의 error 방출은 try_transition(running→error) 게이트. 그 CAS **직전**에
        # 외부 interrupt 를 끼우면 CAS 패배 → cite_forgery error 도 방출 안 됨(stranded error 0).
        if not flipped["done"] and frm == "running" and to == "error":
            flipped["done"] = True
            real_tt(run_id, "running", "interrupted", ended=True)   # forgery CAS 직전 외부 종결
        return real_tt(run_id, frm, to, **kw)

    repo.try_transition = racing_tt
    try:
        evs = list(rs.run("q", thread_id=tid))
    finally:
        repo.try_transition = real_tt

    names = [e.event for e in evs]
    with pool.connection() as conn:
        st = conn.execute("SELECT status::text FROM runs WHERE thread_id=%s", (tid,)).fetchone()[0]
    print(f"\n[G4b] events={names} db_status={st}")
    assert flipped["done"], "forgery CAS 직전 침입 시뮬이 트리거 안 됨"
    assert "error" not in names, "외부종결 run 에 stranded cite_forgery error(CAS 게이트가 막아야)"
    assert "message.completed" not in names    # 위조라 답변 본문 없음
    assert st == "interrupted"                  # DB 부활 없음(CAS 견고)
    assert not _agent_msgs(repo, tid)


# ════════════════════════════════════════════════════════════════════════════
# G5 resume/awaiting/reject 경로 영향
# ════════════════════════════════════════════════════════════════════════════
def test_G5_approve_resume_finalizes_running(pool):
    """승인 일시정지(approval.requested)는 _finalize 안 타고 멈춘다(무관). approve 재개 후
    finalize 가 running 확인하고 정상 답변 영속. reject 는 run.done(rejected)만."""
    repo = ConversationRepository(pool)

    class Appr:
        def stream(self, m, thread_id="default"):
            yield _call_chunk()
            yield {"__interrupt__": (_Interrupt("승인?"),)}

        def resume(self, thread_id="default"):
            yield _tool_chunk(); yield _final_chunk()

        def reject_pending(self, thread_id="default"):
            pass

        settings = _SETTINGS

    # approve 경로
    tid = _make_thread(repo)
    rs = RunService(Appr(), repo)
    evs1 = [e.event for e in rs.run("q", thread_id=tid)]
    assert evs1 == ["run.started", "tool.call", "approval.requested"], evs1
    with pool.connection() as conn:
        st = conn.execute("SELECT status::text FROM runs WHERE thread_id=%s", (tid,)).fetchone()[0]
    assert st == "awaiting_approval"
    evs2 = [e.event for e in rs.resume(tid, approve=True)]
    print(f"\n[G5-approve] resume events={evs2}")
    assert "message.completed" in evs2 and evs2[-1] == "run.done", \
        "approve 재개 후 finalize 가 running 확인하고 답변 영속 못함"
    assert len(_agent_msgs(repo, tid)) == 1

    # reject 경로
    tid2 = _make_thread(repo)
    rs2 = RunService(Appr(), repo)
    list(rs2.run("q", thread_id=tid2))
    evs3 = [(e.event, e.data.get("status")) for e in rs2.resume(tid2, approve=False)]
    print(f"[G5-reject] resume events={evs3}")
    assert evs3 == [("run.done", "rejected")], evs3
    assert not _agent_msgs(repo, tid2), "reject 인데 답변 영속"


# ════════════════════════════════════════════════════════════════════════════
# G6 per-run get_run 비용(고동시성 풀 경합)
# ════════════════════════════════════════════════════════════════════════════
def test_G6_finalize_persists_via_atomic_commit_once(pool):
    """**수정 후**: 답변 영속이 commit_agent_answer(원자 CAS) 1회를 통하고, finalize 가 per-chunk/진입
    get_run 폴링을 안 한다(보호가 진입시점 가드→write-time 원자 CAS 로 이동)."""
    repo = ConversationRepository(pool)
    tid = _make_thread(repo)
    real_commit = repo.commit_agent_answer
    real_get_run = repo.get_run
    cnt = {"commit": 0, "get_run": 0}

    def c_commit(*a, **k):
        cnt["commit"] += 1
        return real_commit(*a, **k)

    def c_get_run(run_id):
        cnt["get_run"] += 1
        return real_get_run(run_id)

    repo.commit_agent_answer = c_commit
    repo.get_run = c_get_run

    class Normal:
        def stream(self, m, thread_id="default"):
            yield _tool_chunk(); yield _tool_chunk(); yield _final_chunk()  # 청크 여러 개

    try:
        list(RunService(Normal(), repo).run("q", thread_id=tid))
    finally:
        repo.commit_agent_answer = real_commit
        repo.get_run = real_get_run
    print(f"\n[G6] commit_agent_answer={cnt['commit']} get_run={cnt['get_run']}")
    assert cnt["commit"] == 1, f"답변 영속 commit 이 {cnt['commit']}회(원자 1회여야)"
    assert cnt["get_run"] == 0, f"finalize 가 get_run 폴링 {cnt['get_run']}회(원자 CAS 로 불필요)"


def test_G6b_finalize_window_width_is_not_negligible(pool):
    """★ 심각도 근거: _finalize 의 get_run~add_message 사이 윈도우는 sub-us 가 아니다.

    그 구간엔 get_thread_citations SELECT(thread ancestor-union 재귀 CTE) 1회가 들어간다 —
    여기서 그 SELECT 의 wall-clock 을 측정. median 수백us~수ms 면, 교차 인스턴스 종결자가
    실제로 그 창에 도착해 TOCTOU(G1/G4b)를 자연 트리거할 수 있음을 뒷받침(이론적 0폭 아님)."""
    import statistics
    repo = ConversationRepository(pool)
    tid = _make_thread(repo)
    repo.freeze_citation({"id": _CID, "kind": "law", "title": "t"})
    repo.add_message(tid, role="agent", content_md="x", citation_ids=[_CID])
    ws = []
    for _ in range(300):
        t0 = time.perf_counter()
        repo.get_thread_citations(tid)   # finalize 가 윈도우 내부에서 부르는 유일 DB 작업
        ws.append((time.perf_counter() - t0) * 1e6)
    ws.sort()
    median = statistics.median(ws)
    p95 = ws[int(len(ws) * 0.95)]
    print(f"\n[G6b] finalize-window interior SELECT us: median={median:.1f} p95={p95:.1f} max={ws[-1]:.1f}")
    # 단정: median > 100us (≫ 0). 이 폭이 교차 인스턴스 race 의 물리적 근거.
    assert median > 100, (
        f"윈도우 내부 DB 작업이 {median:.1f}us — 예상보다 짧음(그래도 0 은 아님). "
        "윈도우는 존재하므로 TOCTOU 결론은 유지.")


# ════════════════════════════════════════════════════════════════════════════
# G7 stranded 중간 이벤트 재연결 노출(benign?)
# ════════════════════════════════════════════════════════════════════════════
def test_G7_stranded_events_on_reconnect_benign(pool):
    """교차 인스턴스 interrupt 후 tool/citation 이벤트가 고seq stranded → 재연결로 노출되나?

    GateAgent 가 tool/citation 청크를 흘리는 동안(running) 외부 interrupt → terminal 영속.
    그 뒤 Last-Event-ID 를 0(처음)으로 재연결 시 stranded 이벤트 + terminal 순서·종료성 확인.
    사용자 영향이 benign(중간 이벤트는 보이되 stream 은 terminal 로 정상 종료)인지 재확인."""
    import asyncio
    agent = GateAgent(chunks=[_call_chunk(), _tool_chunk()])  # final 없음(중간서 interrupt)
    with _client(agent) as c:
        tid = c.post("/threads", json={}).json()["id"]
        rid = c.post(f"/threads/{tid}/messages", json={"message": "q"}).json()["run_id"]
        agent.entered.wait(5)
        _wait_status(c, tid, ("running",))
        # 협조취소 신호 후 gate open → 다음 청크 경계서 interrupted 종결
        c.post(f"/runs/{rid}/interrupt")
        agent.gate.set()
        _wait_status(c, tid, ("interrupted",))
        time.sleep(0.1)

        # 처음(Last-Event-ID 이전)부터 재연결
        async def drain(last):
            return [(ev.event, ev.seq) async for ev in c.app.state.runs.stream(rid, last)]

        full = asyncio.run(asyncio.wait_for(drain(-1), timeout=6))
        names = [e for e, _ in full]
        print(f"\n[G7] full reconnect events={names}")
        # benign: 마지막은 terminal, hang 없음
        assert names[-1] in ("run.done", "error"), "재연결 stream 이 terminal 종료 안 됨(hang)"
        # terminal 은 정확히 마지막 1개(중간에 끼지 않음 — 순서 보장)
        term_idx = [i for i, n in enumerate(names) if n in ("run.done", "error")]
        assert term_idx == [len(names) - 1], f"terminal 이 끝이 아님(순서 위반): {names}"
        # message.completed(orphan 답변)는 없어야(중간 interrupt)
        assert "message.completed" not in names, "interrupt 됐는데 답변이 stranded 됨"


# ── 공통 헬퍼(API 경로 테스트용) ─────────────────────────────────────────────
class GateAgent:
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


def _client(agent):
    return TestClient(create_app(agent_factory=lambda cp: agent))


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
