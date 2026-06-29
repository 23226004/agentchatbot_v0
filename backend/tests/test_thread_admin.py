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


def _client():
    return TestClient(create_app(agent_factory=lambda cp: RunAgent()))


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
        # 스레드는 보존(삭제 안 됨)
        assert c.get(f"/threads/{tid}/messages").status_code == 200


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
