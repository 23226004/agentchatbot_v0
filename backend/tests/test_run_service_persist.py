"""RunService → ConversationRepository 영속 통합테스트 (슬라이스3, 라이브 PG).

페이크 agent.stream + **실제 Postgres** 로 transcript 영속을 실증. 미가동 시 skip.
citation id 는 실제 UUIDv5(point_id) — citations.id 가 UUID 타입이라 임의 문자열 불가.
"""

from __future__ import annotations

import uuid

import pytest

psycopg = pytest.importorskip("psycopg")
pytest.importorskip("psycopg_pool")

from legal_core import ids  # noqa: E402
from legal_core.schemas import AnswerContext, LawRef  # noqa: E402

from backend_app.db import build_pool, run_migrations  # noqa: E402
from backend_app.repositories import ConversationRepository  # noqa: E402
from backend_app.services.run_service import DISCLAIMER, RunService  # noqa: E402


@pytest.fixture(scope="module")
def repo():
    try:
        pool = build_pool()
        run_migrations(pool)
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"convstore postgres 미가동: {exc}")
    yield ConversationRepository(pool)
    pool.close()


class AIMsg:
    def __init__(self, content="", tool_calls=None):
        self.content = content
        self.tool_calls = tool_calls or []


class ToolMessage:
    def __init__(self, tool_call_id, content, artifact=None):
        self.tool_call_id = tool_call_id
        self.content = content
        self.artifact = artifact


_LAW = "099002"   # 고유 law_id — citation 전역 동결이 타 테스트/이전 실행과 충돌하지 않게(테스트 격리)


def _ref(no):
    uri = ids.article_iri(_LAW, "20260227", no)
    return LawRef(id=ids.point_id(uri), kind="law", title="건축법", ref=f"건축법 제{no}조",
                  snippet=f"제{no}조 발췌", url="https://www.law.go.kr/", uri=uri,
                  resource_id=_LAW, eff_date="2026-02-27", score=1.0,
                  article_text=f"제{no}조 전체 본문")


REF1, REF2 = _ref(2), _ref(53)


class FakeAgent:
    def stream(self, message, thread_id="default"):
        yield {"agent": {"messages": [AIMsg(tool_calls=[
            {"id": "c1", "name": "search_legal", "args": {"query": "거실"}}])]}}
        yield {"tools": {"messages": [ToolMessage(
            "c1", "법령 텍스트",
            artifact=AnswerContext(articles=[REF1, REF2], query="거실"))]}}
        yield {"agent": {"messages": [AIMsg(
            content=f"건축법상 거실은 ...이다 [[cite:{REF1.id}]].")]}}


def test_runservice_persists_to_postgres(repo):
    tid = repo.create_thread("persist-test")
    events = list(RunService(FakeAgent(), repo).run("거실이 뭐야?", thread_id=tid))

    # 1) 이벤트 계약 유지
    assert [e.event for e in events] == [
        "run.started", "tool.call", "tool.result",
        "citation.added", "citation.added", "message.completed", "run.done"]
    # seq = thread 별 DB 원자채번(단조·유일). user 메시지 등 이벤트 없는 영속도 seq 소비 → 비연속 가능.
    seqs = [e.seq for e in events]
    assert seqs == sorted(seqs) and len(set(seqs)) == len(seqs)

    pool = repo.pool
    with pool.connection() as conn:
        # 2) run 종결
        st = conn.execute("SELECT status FROM runs WHERE thread_id=%s", (tid,)).fetchone()[0]
        assert st == "completed"
        # 3) citations 전역 동결(전문)
        for ref in (REF1, REF2):
            row = conn.execute("SELECT article_text FROM citations WHERE id=%s", (ref.id,)).fetchone()
            assert row and row[0] == ref.article_text       # snippet 아닌 전문
        # 4) 도구셀 메시지(role=tool, 결과 갱신)
        trow = conn.execute(
            "SELECT tool_name, tool_result FROM messages "
            "WHERE thread_id=%s AND role='tool'", (tid,)).fetchone()
        assert trow[0] == "search_legal"
        # 5) 답변 메시지(role=agent) + message_citations 링크 = 본문 인용(ID1)만
        arow = conn.execute(
            "SELECT id, content_md FROM messages WHERE thread_id=%s AND role='agent'",
            (tid,)).fetchone()
        assert DISCLAIMER in arow[1] and "기준 시행일자: 2026-02-27" in arow[1]
        links = [r[0] for r in conn.execute(
            "SELECT citation_id FROM message_citations WHERE message_id=%s", (arow[0],)).fetchall()]
        assert links == [uuid.UUID(REF1.id)]                # 인용한 1건만 링크(전체 아님)
        # 6) get_thread_messages 로 복원되는 순서(tool→agent, seq 순)
        msgs = repo.get_thread_messages(tid)
        assert [m["role"] for m in msgs] == ["user", "tool", "agent"]   # 사용자 질문도 영속
