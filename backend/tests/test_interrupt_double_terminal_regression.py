"""회귀 재현: interrupt↔approve(resume) 동시 race → SSE 스트림에 terminal 2개(run.done + error).

확정 버그(H2). 근본원인:
  awaiting_approval run 에 interrupt 와 approve 가 동시에 도착.
  - interrupt: interrupt_paused → CAS awaiting→interrupted = True → _push_terminal(run.done) 직접 푸시.
  - approve : RunManager.resume 가 get_active_run='awaiting_approval' 보고 통과 → pump 스폰.
              pump 안에서 RunService.resume 가 CAS awaiting→running 시도 = False(이미 interrupted)
              → ValueError. pump 의 except 가 삼키나 saw_terminal=False·last_name=None →
              finally 가 _synthetic_terminal()=error 를 버퍼에 append.
  결과: 같은 run 버퍼에 run.done(interrupted) + error 두 terminal. 이미 stream 중인 클라가 둘 다 받음.

DB 최종상태(interrupted)는 정확하나 SSE 계약 위반(정상 중지된 run 에 사후 'error' 방출).
재현 확률을 높이기 위해 RunManager.resume 직후 pump 가 도는 윈도우를 동시 발사로 노린다.

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

_URI = ids.article_iri("099003", "20260227", 2)
_CID = ids.point_id(_URI)
_S = types.SimpleNamespace(llm_model="m", provider="p")


@pytest.fixture(scope="module", autouse=True)
def _require_pg():
    try:
        p = build_pool(); p.close()
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"convstore postgres 미가동: {exc}")


class AIMsg:
    def __init__(self, content="", tool_calls=None):
        self.content = content; self.tool_calls = tool_calls or []


class ToolMessage:
    def __init__(self, i, c, artifact=None):
        self.tool_call_id, self.content, self.artifact = i, c, artifact


class _I:
    def __init__(self, v): self.value = v


def _ref():
    return LawRef(id=_CID, kind="law", title="t", ref="r", snippet="s", url="u", uri=_URI,
                  resource_id="099003", eff_date="2026-02-27", score=1.0, article_text="t")


def _tool(): return {"tools": {"messages": [ToolMessage(
    "c1", "t", artifact=AnswerContext(articles=[_ref()], query="q"))]}}
def _call(): return {"agent": {"messages": [AIMsg(tool_calls=[{"id": "c1", "name": "s", "args": {}}])]}}
def _final(): return {"agent": {"messages": [AIMsg(content=f"x [[cite:{_CID}]].")]}}


class ApprovalAgent:
    def stream(self, m, thread_id="default"):
        yield _call(); yield {"__interrupt__": (_I("a"),)}
    def resume(self, thread_id="default"):
        yield _tool(); yield _final()
    def reject_pending(self, thread_id="default"): pass
    settings = _S


def _client(a): return TestClient(create_app(agent_factory=lambda cp: a))
def _events(r): return [l[len("event: "):] for l in r.text.splitlines() if l.startswith("event: ")]


def _run_status(c, rid):
    with c.app.state.repo.pool.connection() as conn:
        return conn.execute("SELECT status::text FROM runs WHERE id=%s", (rid,)).fetchone()[0]


def _await_status(c, rid, statuses, timeout=8.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if _run_status(c, rid) in statuses:
            return
        time.sleep(0.005)
    raise AssertionError(f"{_run_status(c,rid)} not in {statuses}")


def test_interrupt_vs_approve_double_terminal():
    """동시 interrupt+approve 를 다수 trial 반복 → 한 번이라도 버퍼/스트림에 terminal 2개면 버그 확정."""
    hits = []
    trials = 80
    for t in range(trials):
        agent = ApprovalAgent()
        with _client(agent) as c:
            tid = c.post("/threads", json={}).json()["id"]
            rid = c.post(f"/threads/{tid}/messages", json={"message": "q"}).json()["run_id"]
            _await_status(c, rid, ("awaiting_approval",))
            bar = threading.Barrier(2)
            def appr():
                bar.wait(); c.post(f"/threads/{tid}/approve", json={"approve": True})
            def intr():
                bar.wait(); c.post(f"/runs/{rid}/interrupt")
            th = [threading.Thread(target=appr), threading.Thread(target=intr)]
            [x.start() for x in th]; [x.join() for x in th]
            _await_status(c, rid, ("interrupted", "completed", "error"))
            time.sleep(0.15)  # 패자 pump 의 synthetic terminal 까지 정착
            # G4: 인프로세스 버퍼 제거 → 내구 로그(run_events)에서 terminal 수를 센다.
            names = [e["event"] for e in c.app.state.repo.get_run_events_after(rid, -1)]
            terms = [e for e in names if e in ("run.done", "error")]
            if len(terms) > 1:
                hits.append((t, _run_status(c, rid), terms, names))
    print(f"\n[REGRESSION] trials={trials} double_terminal_hits={len(hits)}")
    for h in hits[:10]:
        print(f"  trial={h[0]} db={h[1]} terminals={h[2]} all={h[3]}")
    # 버그가 살아있으면 hits>0. 수정 후엔 0 이어야(회귀 가드).
    assert not hits, (
        f"DOUBLE-TERMINAL BUG REPRODUCED: {len(hits)}/{trials} trials emitted 2 terminals "
        "(run.done(interrupted) + spurious error from losing resume pump's synthetic terminal)")
