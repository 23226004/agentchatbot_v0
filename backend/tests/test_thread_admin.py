"""대화 관리 API — 이름변경(PATCH)·삭제(DELETE)·GET /agents (Do-33).

FastAPI TestClient + 라이브 PG + 스텁 agent(LLM 불요). 삭제는 자식·checkpoint cascade 정합 + global
citations 보존 + running 가드(409) 를 못박는다.
"""

from __future__ import annotations

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


def _ref():
    return LawRef(id=_CID, kind="law", title="건축법", ref="건축법 제2조", snippet="발췌",
                  url="https://www.law.go.kr/", uri=_URI, resource_id="099003",
                  eff_date="2026-02-27", score=1.0, article_text="제2조 전체 — 거실이란 ...")


_SETTINGS = types.SimpleNamespace(llm_model="qwen3.6-35b-a3b", provider="compatible")


class RunAgent:
    """도구 1회(인용 생성) 후 완주 — messages·runs·citations·run_events 를 채운다."""
    settings = _SETTINGS

    def stream(self, message, thread_id="default"):
        yield {"agent": {"messages": [AIMsg(tool_calls=[
            {"id": "c1", "name": "search_legal", "args": {"query": "거실"}}])]}}
        yield {"tools": {"messages": [ToolMessage(
            "c1", "법령 텍스트", artifact=AnswerContext(articles=[_ref()], query="거실"))]}}
        yield {"agent": {"messages": [AIMsg(content=f"건축법상 거실은 ...이다 [[cite:{_CID}]].")]}}

    def resume(self, thread_id="default"):
        yield from ()

    def reject_pending(self, thread_id="default"):
        pass


class ApprovalAgent:
    """도구 전 interrupt(승인대기) — 대기 도구셀(tool_result NULL) 영속. resume 시 실행→완주."""
    settings = _SETTINGS

    def stream(self, message, thread_id="default"):
        yield {"agent": {"messages": [AIMsg(tool_calls=[
            {"id": "c1", "name": "search_legal", "args": {"query": "거실"}}])]}}

        class _I:
            value = "승인?"
        yield {"__interrupt__": (_I(),)}

    def resume(self, thread_id="default"):
        yield {"tools": {"messages": [ToolMessage(
            "c1", "법령", artifact=AnswerContext(articles=[_ref()], query="거실"))]}}
        yield {"agent": {"messages": [AIMsg(content=f"...[[cite:{_CID}]].")]}}

    def reject_pending(self, thread_id="default"):
        pass


def _client(agent=None):
    return TestClient(create_app(agent_factory=lambda cp: agent or RunAgent()))


def _tool_results(c, thread_id):
    with c.app.state.repo.pool.connection() as conn:
        return [r[0] for r in conn.execute(
            "SELECT tool_result FROM messages WHERE thread_id=%s AND role='tool'", (thread_id,)).fetchall()]


def _wait_status(c, thread_id, statuses, timeout=5.0):
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


def _count(c, sql, params):
    with c.app.state.repo.pool.connection() as conn:
        return conn.execute(sql, params).fetchone()[0]


# ── GET /agents ──────────────────────────────────────────────────────────────
def test_agents_lists_legal_profile():
    with _client() as c:
        agents = c.get("/agents").json()
        assert isinstance(agents, list) and len(agents) >= 1
        legal = next(a for a in agents if a["id"] == "legal")
        assert legal["label"] and legal["ready"] is True


# ── PATCH 이름변경 ───────────────────────────────────────────────────────────
def test_rename_thread_updates_title():
    with _client() as c:
        tid = c.post("/threads", json={"title": "원래"}).json()["id"]
        r = c.patch(f"/threads/{tid}", json={"title": "바뀐 제목"})
        assert r.status_code == 200 and r.json()["title"] == "바뀐 제목"
        # 목록에 반영
        threads = c.get("/threads").json()["threads"]
        assert next(t for t in threads if t["id"] == tid)["title"] == "바뀐 제목"


def test_rename_nonexistent_404():
    with _client() as c:
        import uuid
        r = c.patch(f"/threads/{uuid.uuid4()}", json={"title": "x"})
        assert r.status_code == 404


def test_rename_non_uuid_404_not_500():
    with _client() as c:
        assert c.patch("/threads/not-a-uuid", json={"title": "x"}).status_code == 404


def test_rename_non_string_title_422():
    with _client() as c:
        tid = c.post("/threads", json={}).json()["id"]
        assert c.patch(f"/threads/{tid}", json={"title": 123}).status_code == 422


# ── DELETE 삭제 ──────────────────────────────────────────────────────────────
def test_delete_thread_cascades_children_preserves_citations():
    with _client() as c:
        tid = c.post("/threads", json={"title": "삭제대상"}).json()["id"]
        # 한 턴 완주 → messages·runs·run_events·message_citations·citation 영속
        c.post(f"/threads/{tid}/messages", json={"message": "거실이 뭐야?"})
        _wait_status(c, tid, {"completed"})
        assert _count(c, "SELECT count(*) FROM messages WHERE thread_id=%s", (tid,)) > 0
        assert _count(c, "SELECT count(*) FROM runs WHERE thread_id=%s", (tid,)) > 0
        assert _count(c, "SELECT count(*) FROM citations WHERE id=%s", (_CID,)) == 1

        r = c.delete(f"/threads/{tid}")
        assert r.status_code == 200 and r.json()["deleted"] is True

        # thread + 자식 전부 삭제
        assert c.get(f"/threads/{tid}/messages").status_code == 404
        assert _count(c, "SELECT count(*) FROM messages WHERE thread_id=%s", (tid,)) == 0
        assert _count(c, "SELECT count(*) FROM runs WHERE thread_id=%s", (tid,)) == 0
        assert _count(c, "SELECT count(*) FROM run_events WHERE thread_id=%s", (tid,)) == 0
        # ★ global citation 은 보존(불변 법령 스냅샷·타 thread 공유)
        assert _count(c, "SELECT count(*) FROM citations WHERE id=%s", (_CID,)) == 1
        # message_citations 링크는 삭제(메시지 사라짐)
        assert _count(c, "SELECT count(*) FROM message_citations mc JOIN messages m "
                         "ON m.id=mc.message_id WHERE m.thread_id=%s", (tid,)) == 0


def test_delete_nonexistent_404():
    with _client() as c:
        import uuid
        assert c.delete(f"/threads/{uuid.uuid4()}").status_code == 404


def test_delete_non_uuid_404_not_500():
    with _client() as c:
        assert c.delete("/threads/not-a-uuid").status_code == 404


def test_delete_blocked_while_running_409():
    with _client() as c:
        tid = c.post("/threads", json={}).json()["id"]
        # running run 직접 주입(pump 활성 writer 모사) → 삭제 차단
        import uuid
        with c.app.state.repo.pool.connection() as conn:
            conn.execute("INSERT INTO runs (id, thread_id, status) VALUES (%s, %s, 'running')",
                         (str(uuid.uuid4()), tid))
        assert c.delete(f"/threads/{tid}").status_code == 409


def _thread_title(c, thread_id):
    with c.app.state.repo.pool.connection() as conn:
        row = conn.execute("SELECT title FROM threads WHERE id=%s", (thread_id,)).fetchone()
        return row[0] if row else None


def _first_user_msg_id(c, thread_id):
    with c.app.state.repo.pool.connection() as conn:
        row = conn.execute("SELECT id::text FROM messages WHERE thread_id=%s AND role='user' "
                           "ORDER BY seq LIMIT 1", (thread_id,)).fetchone()
        return row[0]


# ── 첫 질문 자동 대화명(최초 1회) + 분기 제목 ────────────────────────────────
def test_first_message_sets_title_once():
    with _client() as c:
        tid = c.post("/threads", json={}).json()["id"]
        assert _thread_title(c, tid) is None                      # 생성 직후 무제목
        c.post(f"/threads/{tid}/messages", json={"message": "건폐율이 무엇인가요?"})
        _wait_status(c, tid, {"completed"})
        assert _thread_title(c, tid) == "건폐율이 무엇인가요?"      # 첫 질문이 제목
        c.post(f"/threads/{tid}/messages", json={"message": "두 번째 질문"})
        _wait_status(c, tid, {"completed"})
        assert _thread_title(c, tid) == "건폐율이 무엇인가요?"      # 최초 1회만 — 안 바뀜


def test_manual_rename_not_overwritten_by_message():
    with _client() as c:
        tid = c.post("/threads", json={"title": "내가 정한 이름"}).json()["id"]
        c.post(f"/threads/{tid}/messages", json={"message": "질문"})
        _wait_status(c, tid, {"completed"})
        assert _thread_title(c, tid) == "내가 정한 이름"           # 수동 제목 보존(IS NULL 아님)


def test_long_title_truncated():
    with _client() as c:
        tid = c.post("/threads", json={}).json()["id"]
        c.post(f"/threads/{tid}/messages", json={"message": "가" * 100})
        _wait_status(c, tid, {"completed"})
        t = _thread_title(c, tid)
        assert t.endswith("…") and len(t) == 61                   # 60자 + …


def test_fork_title_from_fork_point():
    with _client() as c:
        tid = c.post("/threads", json={}).json()["id"]
        c.post(f"/threads/{tid}/messages", json={"message": "원본 분기점 질문"})
        _wait_status(c, tid, {"completed"})
        mid = _first_user_msg_id(c, tid)
        new_id = c.post(f"/threads/{tid}/fork", json={"fork_point_message_id": mid}).json()["thread_id"]
        assert _thread_title(c, new_id) == "원본 분기점 질문"       # 분기 제목 = 분기점 질문


# ── 홀리스틱 교차검증 수정(A1·A2·C) ─────────────────────────────────────────
def test_delete_blocked_with_fork_children_409():
    """A1: fork 후손 있는 부모 삭제 차단(참조모델이라 자식 history 소실 방지). leaf 부터 삭제 가능."""
    with _client() as c:
        parent = c.post("/threads", json={}).json()["id"]
        child = c.post("/threads", json={}).json()["id"]
        with c.app.state.repo.pool.connection() as conn:
            conn.execute("UPDATE threads SET forked_from_thread_id=%s WHERE id=%s", (parent, child))
        assert c.delete(f"/threads/{parent}").status_code == 409   # 후손 있어 차단
        assert c.delete(f"/threads/{child}").status_code == 200    # leaf 삭제 OK
        assert c.delete(f"/threads/{parent}").status_code == 200   # 이제 부모 삭제 OK


def test_reject_fills_orphan_tool_cells():
    """A2: 전량 거절 시 대기 도구셀 tool_result NULL → 마커로 채움(고아 pending 방지)."""
    with _client(ApprovalAgent()) as c:
        tid = c.post("/threads", json={}).json()["id"]
        c.post(f"/threads/{tid}/messages", json={"message": "q"})
        _wait_status(c, tid, {"awaiting_approval"})
        assert _tool_results(c, tid) == [None]                     # 대기 셀 NULL
        c.post(f"/threads/{tid}/approve", json={"approve": False})
        _wait_status(c, tid, {"rejected"})
        assert all(r is not None for r in _tool_results(c, tid))   # 더 이상 NULL 고아 없음


def test_interrupt_awaiting_fills_orphan_tool_cells():
    """A2: 승인대기 run 을 interrupt 종결 시에도 대기 도구셀을 마커로 채움."""
    with _client(ApprovalAgent()) as c:
        tid = c.post("/threads", json={}).json()["id"]
        rid = c.post(f"/threads/{tid}/messages", json={"message": "q"}).json()["run_id"]
        _wait_status(c, tid, {"awaiting_approval"})
        assert c.post(f"/runs/{rid}/interrupt").status_code == 200
        _wait_status(c, tid, {"interrupted"})
        assert all(r is not None for r in _tool_results(c, tid))


def test_gc_sweeps_orphan_checkpoints():
    """C: 부모 thread 없는 고아 checkpoint* 를 gc 가 회수(FK 부재 누수 차단). live 는 보존."""
    with _client() as c:
        repo = c.app.state.repo
        import uuid
        orphan_tid = str(uuid.uuid4())
        live_tid = c.post("/threads", json={}).json()["id"]
        with repo.pool.connection() as conn:
            for t in ("checkpoints", "checkpoint_writes", "checkpoint_blobs"):
                # 컬럼이 테이블마다 다르므로 공통 thread_id/checkpoint_ns 만 채워 최소 행 삽입
                pass
            conn.execute("INSERT INTO checkpoints (thread_id, checkpoint_ns, checkpoint_id, type, checkpoint, metadata) "
                         "VALUES (%s,'','cp1','', '{}','{}')", (orphan_tid,))
            conn.execute("INSERT INTO checkpoints (thread_id, checkpoint_ns, checkpoint_id, type, checkpoint, metadata) "
                         "VALUES (%s,'','cp2','', '{}','{}')", (live_tid,))
        swept = repo.gc_orphan_checkpoints()
        assert swept >= 1
        n_orphan = _count(c, "SELECT count(*) FROM checkpoints WHERE thread_id=%s", (orphan_tid,))
        n_live = _count(c, "SELECT count(*) FROM checkpoints WHERE thread_id=%s", (live_tid,))
        assert n_orphan == 0 and n_live == 1                       # 고아만 sweep, live 보존


# ── 고아 run_events GC sweep (Do-33-XV 🔴 — delete 후 늦은 terminal append) ───
def _insert_orphan_event(repo, age_days: int):
    import uuid
    run_id = str(uuid.uuid4())
    with repo.pool.connection() as conn:
        conn.execute(
            "INSERT INTO run_events (thread_id, run_id, seq, event, data, created_at) "
            "VALUES (%s, %s, 1, 'run.done', '{}'::jsonb, now() - make_interval(days => %s))",
            (str(uuid.uuid4()), run_id, age_days))
    return run_id


def _orphan_count(repo, run_id):
    with repo.pool.connection() as conn:
        return conn.execute("SELECT count(*) FROM run_events WHERE run_id=%s", (run_id,)).fetchone()[0]


def test_gc_sweeps_old_orphan_run_events():
    """부모 run 없는 고아 run_events(오래됨) → GC 가 청소(과거: USING runs JOIN 이 영영 미매칭=영구누수)."""
    with _client() as c:
        repo = c.app.state.repo
        rid = _insert_orphan_event(repo, age_days=2)
        assert _orphan_count(repo, rid) == 1
        repo.gc_run_events(retention_seconds=1)          # 2일 전 > 1s retention → sweep
        assert _orphan_count(repo, rid) == 0


def test_gc_keeps_recent_orphan_run_events():
    """retention 안쪽(최근) 고아는 보존 — 나이 가드(run-먼저-생성 불변식 방어심층)."""
    with _client() as c:
        repo = c.app.state.repo
        rid = _insert_orphan_event(repo, age_days=0)     # 방금 생성
        repo.gc_run_events(retention_seconds=3600)       # 1시간 retention → 보존
        assert _orphan_count(repo, rid) == 1
        # 정리
        with repo.pool.connection() as conn:
            conn.execute("DELETE FROM run_events WHERE run_id=%s", (rid,))
