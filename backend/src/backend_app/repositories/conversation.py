"""ConversationRepository — transcript 영속 (Design §3·§4·§6).

단일 writer(RunService)가 호출. 핵심 불변식:
- **seq 원자 채번**(M1): `UPDATE threads SET last_seq=last_seq+1 ... RETURNING`.
- **thread당 활성 run 1개**(M1): partial unique index 위반 → ActiveRunExists(=409).
- **citation 전역 동결**(A): citations `ON CONFLICT(id) DO NOTHING`(첫 인용 본문 영속).
- **fork = 참조**(C2): 메시지 복사 없이 조상 prefix를 재귀 UNION + ORDER BY seq.

psycopg3 ConnectionPool 주입. 메서드별 원자(같은 tx). 외부 라이브러리 의존은 이 계층에 격리.
"""

from __future__ import annotations

import uuid
from typing import Any

import psycopg
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

# 활성(비종료) run 상태 — partial unique index(one_active_run_per_thread, 마이그0003)와 공유하는 단일 정의.
# **'interrupted' 제외**: 사용자 중지로 종료된 터미널 상태(마이그0003) — 활성이면 thread 가 잠겨 새 run 불가.
ACTIVE_STATUSES = ("running", "awaiting_approval")


class _RunNotRunning(Exception):
    """commit_agent_answer 내부 신호 — run 이 running 이 아니라 답변 영속 롤백(외부 종결). 외부 미노출."""


_UNSET = object()   # put_settings 부분갱신: "이 필드는 전송 안 됨(보존)" — None(명시 비움)과 구분.
# 부팅 reconcile 대상 — **awaiting_approval 제외**. 승인대기는 durable·재개 가능(checkpoint 영속)이라
# 부팅마다 죽이면 미결 승인이 전량 소실된다(§6 "재개 가능한 건만"·§8 awaiting=TTL 전용). 교차검증 발견.
# **'interrupted' 제외**: 사용자 중지로 이미 종료(ended_at)된 터미널이라 reconcile 대상 아님.
RECONCILE_STATUSES = ("running",)

# out-of-process 비정상 종결(프로세스 크래시) 시 sweep 이 run 을 error 로 회수할 때, 그 run 의
# 결과 미도착 도구셀도 함께 정합화하는 마커(XV D-2 고아 방지). run_service._INTERRUPTED_TOOL 과 동일 문구.
_INTERRUPTED_TOOL_MARKER = "실행이 중단되어 결과를 받지 못했습니다."


class ActiveRunExists(Exception):
    """thread 에 이미 활성 run 이 있음(동시 run 금지) → API 409."""


def _uuid() -> str:
    return str(uuid.uuid4())


class ConversationRepository:
    def __init__(self, pool: ConnectionPool) -> None:
        self.pool = pool

    # ── threads ────────────────────────────────────────────────────────────
    def create_thread(self, title: str | None = None, owner_id: str | None = None) -> str:
        tid = _uuid()
        with self.pool.connection() as conn:
            conn.execute(
                "INSERT INTO threads (id, title, owner_id) VALUES (%s, %s, %s)",
                (tid, title, owner_id),
            )
        return tid

    def list_threads(self, owner_id: str | None = None, limit: int = 100) -> list[dict]:
        """스레드 목록(FE 사이드바). owner_id 주면 그 소유자만, 최근 갱신순. (owner_id, updated_at) 인덱스 활용."""
        cols = "id::text, title, owner_id::text, forked_from_thread_id::text, created_at, updated_at"
        with self.pool.connection() as conn:
            if owner_id is not None:
                rows = conn.execute(
                    f"SELECT {cols} FROM threads WHERE owner_id = %s "
                    "ORDER BY updated_at DESC LIMIT %s", (owner_id, limit)).fetchall()
            else:
                rows = conn.execute(
                    f"SELECT {cols} FROM threads ORDER BY updated_at DESC LIMIT %s", (limit,)).fetchall()
            keys = ("id", "title", "owner_id", "forked_from_thread_id", "created_at", "updated_at")
            return [dict(zip(keys, r)) for r in rows]

    def thread_exists(self, thread_id: str) -> bool:
        """thread 존재 여부(API 입력검증용). 잘못된 UUID 는 False(404 매핑)."""
        try:
            uuid.UUID(str(thread_id))
        except ValueError:
            return False
        with self.pool.connection() as conn:
            return conn.execute("SELECT 1 FROM threads WHERE id = %s", (thread_id,)).fetchone() is not None

    def rename_thread(self, thread_id: str, title: str) -> bool:
        """대화명 변경. 존재하면 title 갱신(updated_at 도)·True, 없으면 False(404 매핑)."""
        try:
            uuid.UUID(str(thread_id))
        except ValueError:
            return False
        with self.pool.connection() as conn:
            cur = conn.execute(
                "UPDATE threads SET title = %s, updated_at = now() WHERE id = %s",
                (title, thread_id),
            )
            return cur.rowcount > 0

    def has_running_run(self, thread_id: str) -> bool:
        """thread 에 **실행 중**(running) run 이 있나 — 삭제 가드용. awaiting_approval/interrupted(일시정지)는
        제외(삭제로 취소 허용). running 은 pump 가 활성 writer 라 삭제 시 경쟁 → 차단(409)."""
        try:
            uuid.UUID(str(thread_id))
        except ValueError:
            return False
        with self.pool.connection() as conn:
            row = conn.execute(
                "SELECT 1 FROM runs WHERE thread_id = %s AND status = 'running' LIMIT 1",
                (thread_id,),
            ).fetchone()
            return row is not None

    def delete_thread(self, thread_id: str) -> bool:
        """대화 삭제 — 자식 전부 + LangGraph checkpoint* 를 **단일 트랜잭션**으로 cascade 삭제(FK 순서 준수).
        존재했으면 True, 아니면 False(404). **global citations 는 보존**(불변 법령 스냅샷·타 thread 공유);
        message_citations 링크만 삭제. forked_from 으로 이 thread 를 가리키던 자식은 NULL 로(계보 포인터 해제)."""
        try:
            uuid.UUID(str(thread_id))
        except ValueError:
            return False
        # ★ `conn.transaction()` 필수: 풀은 autocommit=True(LangGraph CREATE INDEX CONCURRENTLY 위함)라
        # `with pool.connection()` 만으론 각 execute 가 즉시 commit → 중간 실패(statement_timeout/데드락) 시
        # 부분삭제(thread 잔존+자식 일부 소실)가 남는다. 명시 트랜잭션으로 전부-or-전무 보장(교차검증 🔴 수정).
        with self.pool.connection() as conn, conn.transaction():
            # 자식부터(FK: message_citations→messages, messages→runs)
            conn.execute(
                "DELETE FROM message_citations WHERE message_id IN "
                "(SELECT id FROM messages WHERE thread_id = %s)", (thread_id,))
            conn.execute("DELETE FROM messages WHERE thread_id = %s", (thread_id,))
            conn.execute("DELETE FROM run_events WHERE thread_id = %s", (thread_id,))
            conn.execute("DELETE FROM runs WHERE thread_id = %s", (thread_id,))
            conn.execute("DELETE FROM summaries WHERE thread_id = %s", (thread_id,))
            # LangGraph checkpoint 상태(thread_id 키) — 고아 방지. checkpoint_migrations 는 전역이라 제외.
            for t in ("checkpoint_writes", "checkpoint_blobs", "checkpoints"):
                conn.execute(f"DELETE FROM {t} WHERE thread_id = %s", (thread_id,))
            # 이 thread 를 forked_from 으로 참조하던 다른 thread 의 포인터 해제(FK 위반 방지)
            conn.execute(
                "UPDATE threads SET forked_from_thread_id = NULL WHERE forked_from_thread_id = %s",
                (thread_id,))
            cur = conn.execute("DELETE FROM threads WHERE id = %s", (thread_id,))
            return cur.rowcount > 0

    def next_seq(self, thread_id: str) -> int:
        """thread 내 단조 seq 원자 채번(M1). 단일 UPDATE...RETURNING."""
        with self.pool.connection() as conn:
            row = conn.execute(
                "UPDATE threads SET last_seq = last_seq + 1, updated_at = now() "
                "WHERE id = %s RETURNING last_seq",
                (thread_id,),
            ).fetchone()
        if row is None:
            raise KeyError(f"thread not found: {thread_id}")
        return row[0]

    # ── runs ───────────────────────────────────────────────────────────────
    def open_run(self, thread_id: str, model: str | None = None) -> str:
        """활성 run 시작. 이미 활성 run 이 있으면 ActiveRunExists(409). model = 이 턴에 선택된 LLM."""
        rid = _uuid()
        try:
            with self.pool.connection() as conn:
                conn.execute(
                    "INSERT INTO runs (id, thread_id, status, model) VALUES (%s, %s, 'running', %s)",
                    (rid, thread_id, model),
                )
        except psycopg.errors.UniqueViolation as exc:
            # one_active_run_per_thread partial index 위반
            raise ActiveRunExists(thread_id) from exc
        return rid

    def get_run_model(self, run_id: str) -> str | None:
        """run 에 기록된 모델(resume 이 같은 모델로 이어가도록). 없으면 None."""
        try:
            uuid.UUID(str(run_id))
        except ValueError:
            return None
        with self.pool.connection() as conn:
            row = conn.execute("SELECT model FROM runs WHERE id = %s", (run_id,)).fetchone()
        return row[0] if row else None

    def set_run_status(self, run_id: str, status: str, *, ended: bool = False) -> None:
        with self.pool.connection() as conn:
            conn.execute(
                "UPDATE runs SET status = %s::run_status, "
                "ended_at = CASE WHEN %s THEN now() ELSE ended_at END WHERE id = %s",
                (status, ended, run_id),
            )

    def reconcile_orphan_runs(self, stale_seconds: int = 30) -> int:
        """고아 run 정리(M1-partial/M2-a): **heartbeat 가 stale 한** running → error 전이(G4 멀티워커).

        **liveness 인지**(교차검증 HIGH 수정): 부팅마다 모든 running 을 죽이면 멀티워커/롤링재시작서
        살아있는 워커의 run 까지 오살했다. 각 인스턴스가 활성 run 의 heartbeat_at 를 갱신하므로,
        heartbeat 가 stale_seconds 초과(소유 워커 사망)한 run 만 쓴다 → 살아있는 워커 run 보존.
        부팅·주기 유지보수 양쪽서 호출 가능(idempotent). interrupted 는 터미널이라 제외, **awaiting_approval
        은 제외**(durable — TTL(`timeout_stale_approvals`)로만 만료). 반환=전이 건수.
        """
        import json
        with self.pool.connection() as conn:
            cur = conn.execute(
                "WITH swept AS ("
                "  UPDATE runs SET status = 'error', ended_at = now() "
                "  WHERE status = ANY(%s::run_status[]) "
                "  AND heartbeat_at < now() - make_interval(secs => %s) RETURNING id), "
                "orphans AS ("
                "  UPDATE messages SET tool_result = %s "
                "  WHERE role = 'tool' AND tool_result IS NULL "
                "  AND run_id IN (SELECT id FROM swept)) "
                "SELECT count(*) FROM swept",
                (list(RECONCILE_STATUSES), stale_seconds, json.dumps(_INTERRUPTED_TOOL_MARKER)),
            )
            return cur.fetchone()[0]

    def heartbeat_runs(self, run_ids: list[str]) -> int:
        """이 워커가 구동 중인 running run 들의 heartbeat_at 를 now() 로 갱신(liveness 신호, G4).

        reconcile/timeout sweep 이 살아있는 run 을 죽이지 않게 한다. running 만 갱신(터미널·승인대기 무관)."""
        if not run_ids:
            return 0
        with self.pool.connection() as conn:
            cur = conn.execute(
                "UPDATE runs SET heartbeat_at = now() "
                "WHERE id = ANY(%s::uuid[]) AND status = 'running'",
                (run_ids,),
            )
            return cur.rowcount

    def try_transition(self, run_id: str, from_status: str, to_status: str,
                       *, ended: bool = False) -> bool:
        """조건부(CAS) run 상태 전이 — `from_status` 일 때만 `to_status` 로. 전이 성공=True.

        동시 resume 보호: awaiting_approval→running 을 무가드 UPDATE 로 하면 둘 다 통과해
        중복 처리(교차검증 Medium). 이 CAS 로 단 1명만 진행하게 한다.
        """
        with self.pool.connection() as conn:
            cur = conn.execute(
                "UPDATE runs SET status = %s::run_status, "
                "ended_at = CASE WHEN %s THEN now() ELSE ended_at END "
                "WHERE id = %s AND status = %s::run_status",
                (to_status, ended, run_id, from_status),
            )
            return cur.rowcount == 1

    # ── messages (+ citations 링크 원자) ─────────────────────────────────────
    def add_message(
        self,
        thread_id: str,
        *,
        role: str,
        run_id: str | None = None,
        seq: int | None = None,
        content_md: str | None = None,
        tool_name: str | None = None,
        tool_args: Any = None,
        tool_result: Any = None,
        approval_state: str | None = None,
        parent_id: str | None = None,
        citation_ids: list[str] | None = None,
        tool_call_id: str | None = None,
        model: str | None = None,
    ) -> tuple[str, int]:
        """메시지 1건 영속. seq 미지정 시 원자 채번. citation_ids 주면 같은 tx 에서 링크.

        §6 "seq+message 원자 INSERT" + §4 message_citations 링크를 한 트랜잭션으로 묶는다.
        citation_ids 는 citations 에 **이미 동결된**(freeze_citation) id 여야 함(FK).
        반환 (message_id, seq).
        """
        import json

        mid = _uuid()
        with self.pool.connection() as conn:
            with conn.transaction():
                if seq is None:
                    row = conn.execute(
                        "UPDATE threads SET last_seq = last_seq + 1, updated_at = now() "
                        "WHERE id = %s RETURNING last_seq",
                        (thread_id,),
                    ).fetchone()
                    if row is None:
                        raise KeyError(f"thread not found: {thread_id}")
                    seq = row[0]
                conn.execute(
                    "INSERT INTO messages "
                    "(id, thread_id, run_id, seq, parent_id, role, content_md, "
                    " tool_name, tool_args, tool_result, approval_state, tool_call_id, model) "
                    "VALUES (%s,%s,%s,%s,%s,%s::message_role,%s,%s,%s,%s,%s,%s,%s)",
                    (mid, thread_id, run_id, seq, parent_id, role, content_md, tool_name,
                     json.dumps(tool_args) if tool_args is not None else None,
                     json.dumps(tool_result) if tool_result is not None else None,
                     approval_state, tool_call_id, model),
                )
                for cid in citation_ids or []:
                    conn.execute(
                        "INSERT INTO message_citations (message_id, citation_id) "
                        "VALUES (%s, %s) ON CONFLICT DO NOTHING",
                        (mid, cid),
                    )
        return mid, seq

    def commit_agent_answer(self, thread_id: str, run_id: str, *, content_md: str,
                            citation_ids: list[str] | None = None, parent_id: str | None = None,
                            model: str | None = None) -> tuple[str, int] | None:
        """답변(role=agent) 영속 + `running→completed` 전이를 **한 트랜잭션(원자)**으로 — orphan 차단.

        **CAS 게이트가 영속과 원자**(교차검증): `_finalize` 진입 시점만 보던 가드는 get_run~INSERT 사이
        ~2ms TOCTOU 창에 교차 인스턴스 interrupt 가 끼면 interrupted run 에 완성 답변(orphan)을 남겼다.
        여기선 `UPDATE runs ... WHERE status='running'` 이 0행이면(외부 종결) **전체 롤백→메시지 미영속**,
        None 반환. 성공(1행)시에만 seq 채번+메시지+citation 링크를 같은 tx 로 → (mid, seq).
        """
        mid = _uuid()
        try:
            with self.pool.connection() as conn:
                with conn.transaction():
                    cur = conn.execute(
                        "UPDATE runs SET status = 'completed', ended_at = now() "
                        "WHERE id = %s AND status = 'running'", (run_id,))
                    if cur.rowcount != 1:
                        raise _RunNotRunning  # 외부 종결 → 롤백(답변 미영속)
                    row = conn.execute(
                        "UPDATE threads SET last_seq = last_seq + 1, updated_at = now() "
                        "WHERE id = %s RETURNING last_seq", (thread_id,)).fetchone()
                    if row is None:
                        raise KeyError(f"thread not found: {thread_id}")
                    seq = row[0]
                    conn.execute(
                        "INSERT INTO messages (id, thread_id, run_id, seq, parent_id, role, "
                        " content_md, model) VALUES (%s,%s,%s,%s,%s,'agent',%s,%s)",
                        (mid, thread_id, run_id, seq, parent_id, content_md, model))
                    for cid in citation_ids or []:
                        conn.execute(
                            "INSERT INTO message_citations (message_id, citation_id) "
                            "VALUES (%s, %s) ON CONFLICT DO NOTHING", (mid, cid))
        except _RunNotRunning:
            return None
        return mid, seq

    def get_turn_root(self, run_id: str) -> str | None:
        """run 의 턴 루트(role=user 메시지 id) — 도구셀·답변을 parent_id 로 그룹핑(§3.2)."""
        with self.pool.connection() as conn:
            row = conn.execute(
                "SELECT id::text FROM messages WHERE run_id = %s AND role = 'user' "
                "ORDER BY seq LIMIT 1", (run_id,)).fetchone()
        return row[0] if row else None

    # ── settings (FE §4.1) ───────────────────────────────────────────────────
    def get_settings(self, scope: str = "global") -> dict | None:
        with self.pool.connection() as conn:
            with conn.cursor(row_factory=dict_row) as cur:
                cur.execute("SELECT scope, model, server_url, theme FROM settings WHERE scope = %s",
                            (scope,))
                return cur.fetchone()

    def put_settings(self, scope: str, model: object = _UNSET, server_url: object = _UNSET,
                     theme: object = _UNSET) -> None:
        """**부분 갱신**: 전송된 필드만 SET, 미전송(_UNSET)은 **기존값 보존**(명시 None 은 비움).

        이전엔 무조건 3컬럼 전부 EXCLUDED 로 덮어, model 만 PUT 하면 server_url/theme 가 NULL 로 말소되고
        동시 부분 PUT 이 서로를 lost-update 했다(교차검증 HIGH). CASE WHEN <unset> THEN 기존 ELSE 새값 으로
        필드별 보존 → FE 의 model-만-PUT 흐름·동시 부분 PUT 안전.
        """
        with self.pool.connection() as conn:
            conn.execute(
                "INSERT INTO settings (scope, model, server_url, theme, updated_at) "
                "VALUES (%s,%s,%s,%s, now()) "
                "ON CONFLICT (scope) DO UPDATE SET "
                "  model      = CASE WHEN %s THEN settings.model      ELSE EXCLUDED.model      END, "
                "  server_url = CASE WHEN %s THEN settings.server_url ELSE EXCLUDED.server_url END, "
                "  theme      = CASE WHEN %s THEN settings.theme      ELSE EXCLUDED.theme      END, "
                "  updated_at = now()",
                (scope,
                 None if model is _UNSET else model,
                 None if server_url is _UNSET else server_url,
                 None if theme is _UNSET else theme,
                 model is _UNSET, server_url is _UNSET, theme is _UNSET))

    # ── summaries (FE §4.1, 긴 대화 압축) ────────────────────────────────────
    def add_summary(self, thread_id: str, covers_from_seq: int | None,
                    covers_to_seq: int | None, content_md: str) -> str:
        sid = _uuid()
        with self.pool.connection() as conn:
            conn.execute(
                "INSERT INTO summaries (id, thread_id, covers_from_seq, covers_to_seq, content_md) "
                "VALUES (%s,%s,%s,%s,%s)",
                (sid, thread_id, covers_from_seq, covers_to_seq, content_md))
        return sid

    def get_summaries(self, thread_id: str) -> list[dict]:
        with self.pool.connection() as conn:
            with conn.cursor(row_factory=dict_row) as cur:
                cur.execute(
                    "SELECT id::text, covers_from_seq, covers_to_seq, content_md, created_at "
                    "FROM summaries WHERE thread_id = %s ORDER BY created_at", (thread_id,))
                return cur.fetchall()

    def set_tool_result(self, run_id: str, tool_call_id: str, tool_result: Any) -> None:
        """도구셀 결과를 (run_id, tool_call_id)로 갱신 — tool.call(run)과 tool.result(resume)가
        다른 호출에 걸쳐 일어나도 DB키로 상관(승인 흐름, 슬라이스4)."""
        import json
        with self.pool.connection() as conn:
            conn.execute(
                "UPDATE messages SET tool_result = %s WHERE run_id = %s AND tool_call_id = %s",
                (json.dumps(tool_result) if tool_result is not None else None, run_id, tool_call_id),
            )

    def mark_partial_approval(self, run_id: str, rejected_ids: list[str], marker: Any) -> None:
        """per-tool Stage 2 선택적 승인 셀 분류를 **단일 트랜잭션**으로(비원자 반-마킹 방지, XV S5):
        거절 도구셀 → approval_state='rejected' + 거부 마커(non-NULL), 그 외 미결(NULL) 도구셀 → approved.
        한 tx 안에서 거절을 먼저 채워(non-NULL) approve 대상(NULL)에서 제외 → 승인분만 approved 가 된다.
        도중 실패 시 전부 롤백(반-마킹 상태 불가)."""
        import json
        with self.pool.connection() as conn, conn.transaction():
            if rejected_ids:
                conn.execute(
                    "UPDATE messages SET approval_state = 'rejected', tool_result = %s "
                    "WHERE run_id = %s AND role = 'tool' AND tool_call_id = ANY(%s)",
                    (json.dumps(marker), run_id, list(rejected_ids)),
                )
            conn.execute(
                "UPDATE messages SET approval_state = 'approved' "
                "WHERE run_id = %s AND role = 'tool' AND tool_result IS NULL",
                (run_id,),
            )

    def run_status_counts(self) -> dict[str, int]:
        """G6 메트릭: run 상태별 수(running/awaiting_approval/completed/error/interrupted/rejected).
        runs 테이블 직접 집계 — 별도 계측 없이 상태기계 현황 노출."""
        with self.pool.connection() as conn:
            rows = conn.execute(
                "SELECT status::text, count(*) FROM runs GROUP BY status").fetchall()
        return {r[0]: int(r[1]) for r in rows}

    def resolve_orphan_tool_cells(self, run_id: str, marker: Any) -> None:
        """비정상 종결(취소/크래시) 시 결과 미도착(NULL) 도구셀을 마커로 채워 **'영구 pending' 고아 방지**
        (XV D-2). approval_state 는 보존(사용자 거절이 아님) — tool_result 만 채워 셀을 종결 표시한다.
        멱등(이미 결과 있는 셀은 미변경)."""
        import json
        with self.pool.connection() as conn:
            conn.execute(
                "UPDATE messages SET tool_result = %s "
                "WHERE run_id = %s AND role = 'tool' AND tool_result IS NULL",
                (json.dumps(marker), run_id),
            )

    # ── 승인/재개 (슬라이스4) ────────────────────────────────────────────────
    def get_run(self, run_id: str) -> tuple[str, str] | None:
        """run 의 (thread_id, status) — 없으면 None. interrupt 가 run_id 로 조회(잘못된 UUID→None=404)."""
        try:
            uuid.UUID(str(run_id))
        except ValueError:
            return None
        with self.pool.connection() as conn:
            row = conn.execute(
                "SELECT thread_id::text, status::text FROM runs WHERE id = %s", (run_id,)).fetchone()
        return (row[0], row[1]) if row else None

    def get_active_run(self, thread_id: str) -> tuple[str, str] | None:
        """thread 의 활성 run (id, status) — 없으면 None. resume 이 awaiting_approval run 을 찾는다."""
        with self.pool.connection() as conn:
            row = conn.execute(
                "SELECT id::text, status::text FROM runs "
                "WHERE thread_id = %s AND status = ANY(%s::run_status[]) "
                "ORDER BY started_at DESC LIMIT 1",
                (thread_id, list(ACTIVE_STATUSES)),
            ).fetchone()
        return (row[0], row[1]) if row else None

    # ── run_events (SSE 내구 로그, G4) ───────────────────────────────────────
    def append_run_event(self, thread_id: str, run_id: str, seq: int,
                         event: str, data: dict) -> None:
        """SSE 이벤트를 내구 로그에 영속 — 교차 인스턴스 stream·Last-Event-ID 재연결 근거.

        멱등(ON CONFLICT (run_id, seq) DO NOTHING): pump 재시도·중복 방출에도 1행."""
        import json  # noqa: PLC0415
        with self.pool.connection() as conn:
            conn.execute(
                "INSERT INTO run_events (thread_id, run_id, seq, event, data) "
                "VALUES (%s, %s, %s, %s, %s::jsonb) ON CONFLICT (run_id, seq) DO NOTHING",
                (thread_id, run_id, seq, event, json.dumps(data, ensure_ascii=False)),
            )

    def get_run_events_after(self, run_id: str, after_seq: int = -1,
                             limit: int = 1000) -> list[dict]:
        """run 의 seq > after_seq 이벤트(순서대로). stream tail·Last-Event-ID 재연결 커서.

        잘못된 UUID 는 [](호출부가 get_run 으로 404 선검증하나 방어). data 는 JSONB→dict."""
        try:
            uuid.UUID(str(run_id))
        except ValueError:
            return []
        with self.pool.connection() as conn:
            rows = conn.execute(
                "SELECT seq, event, data FROM run_events "
                "WHERE run_id = %s AND seq > %s ORDER BY seq LIMIT %s",
                (run_id, after_seq, limit),
            ).fetchall()
        return [{"seq": r[0], "event": r[1], "data": r[2]} for r in rows]

    def gc_run_events(self, retention_seconds: int) -> int:
        """내구 이벤트로그 정리(G4 백로그): **종결된 지 retention 초 지난** run 의 이벤트 삭제.

        run_events 는 SSE 라이브/재연결용 임시 로그 — 무한 누적 방지. **transcript(messages)는 영구 보존**
        이라 대화 기록엔 영향 없다. **활성 run(ended_at NULL = running/awaiting_approval)은 절대 삭제 안 함**
        (stream/resume 진행 중일 수 있음). 멀티 인스턴스서 여러 곳이 호출해도 멱등(이미 지운 행은 no-op).
        반환=삭제 행수.

        **고아 sweep(교차검증 🔴 수정)**: run_events 는 FK 가 없다. 대화 삭제(delete_thread)가 runs 를
        지운 뒤, pump 가 completed 전이 직후의 늦은 terminal(run.done 등)을 append 하면 부모 run 이 없는
        **고아 run_events** 가 생긴다 — 위 `USING runs` GC 는 run 이 사라져 영영 매칭 못해 **영구 누수**였다.
        → 부모 run 이 없고 retention 지난 고아도 함께 삭제(나이 가드는 run-먼저-생성 불변식의 방어심층).
        """
        with self.pool.connection() as conn:
            cur = conn.execute(
                "DELETE FROM run_events re USING runs r "
                "WHERE re.run_id = r.id AND r.ended_at IS NOT NULL "
                "AND r.ended_at < now() - make_interval(secs => %s)",
                (retention_seconds,),
            )
            deleted = cur.rowcount
            orphan = conn.execute(
                "DELETE FROM run_events re "
                "WHERE re.created_at < now() - make_interval(secs => %s) "
                "AND NOT EXISTS (SELECT 1 FROM runs r WHERE r.id = re.run_id)",
                (retention_seconds,),
            )
            return deleted + orphan.rowcount

    def get_pending_tool_calls(self, run_id: str) -> list[dict]:
        """이 run 의 **미실행(승인 대기) 도구호출** — approval.requested 에 실어 FE 가 무엇을 승인하는지
        표시(per-tool 정보노출). role=tool·tool_result NULL(아직 실행 전). tool_args 는 JSONB→dict.
        """
        with self.pool.connection() as conn:
            rows = conn.execute(
                "SELECT tool_call_id, tool_name, tool_args FROM messages "
                "WHERE run_id = %s AND role = 'tool' AND tool_result IS NULL "
                "ORDER BY seq", (run_id,),
            ).fetchall()
        return [{"id": r[0], "name": r[1], "args": r[2]} for r in rows]

    def set_pending_approval(self, run_id: str, state: str) -> None:
        """이 run 의 결과 미도착 도구셀들 approval_state 갱신(pending/approved/rejected)."""
        with self.pool.connection() as conn:
            conn.execute(
                "UPDATE messages SET approval_state = %s "
                "WHERE run_id = %s AND role = 'tool' AND tool_result IS NULL",
                (state, run_id),
            )

    def timeout_stale_approvals(self, ttl_seconds: int) -> int:
        """awaiting_approval 이 TTL 초과면 error 전이(§8, thread 영구잠금 방지). 반환=전이 건수.

        승인대기는 heartbeat 안 받으므로(heartbeat_runs 는 running 만) started_at 기준 — 대기시간 만료."""
        return self._timeout("awaiting_approval", ttl_seconds, column="started_at")

    def timeout_stale_runs(self, ttl_seconds: int) -> int:
        """running 이 TTL 초과면 error 전이 — **연결 끊긴 고아 run 회수**.

        디커플(§7)로 고아 자체가 줄었으나 잔여 안전망. **heartbeat_at 기준**(G4/G5): 소유 워커가
        살아있으면 heartbeat 가 갱신돼 ttl 초과 안 함 → 긴 정상 run 을 오살하지 않는다(started_at 기준의
        장기 run 오살 문제 해소). ttl 은 heartbeat 주기보다 충분히 커야. 반환=전이 건수.
        """
        return self._timeout("running", ttl_seconds, column="heartbeat_at")

    def _timeout(self, status: str, ttl_seconds: int, column: str = "started_at") -> int:
        # column 은 내부 고정 리터럴("heartbeat_at"/"started_at") — 사용자 입력 아님(f-string 안전).
        # CTE: 타임아웃 run 을 error 로 전이하며 그 run 의 미결(NULL) 도구셀도 마커로 정합(XV D-2 고아 방지).
        import json
        with self.pool.connection() as conn:
            cur = conn.execute(
                f"WITH swept AS ("  # noqa: S608
                f"  UPDATE runs SET status = 'error', ended_at = now() "
                f"  WHERE status = %s::run_status "
                f"  AND {column} < now() - make_interval(secs => %s) RETURNING id), "
                f"orphans AS ("
                f"  UPDATE messages SET tool_result = %s "
                f"  WHERE role = 'tool' AND tool_result IS NULL "
                f"  AND run_id IN (SELECT id FROM swept)) "
                f"SELECT count(*) FROM swept",
                (status, ttl_seconds, json.dumps(_INTERRUPTED_TOOL_MARKER)),
            )
            return cur.fetchone()[0]

    # ── citations (전역 동결) ────────────────────────────────────────────────
    def freeze_citation(self, citation: dict[str, Any]) -> None:
        """citation.added 방출 순간 전문 동결(A). ON CONFLICT DO NOTHING = 첫-동결 권위.

        citation: {id, kind, title, ref, snippet, url, article_text, law_uri, resource_id, eff_date}
        """
        with self.pool.connection() as conn:
            conn.execute(
                "INSERT INTO citations "
                "(id, kind, title, ref, snippet, url, article_text, law_uri, resource_id, eff_date) "
                "VALUES (%(id)s,%(kind)s,%(title)s,%(ref)s,%(snippet)s,%(url)s,"
                " %(article_text)s,%(law_uri)s,%(resource_id)s,%(eff_date)s) "
                "ON CONFLICT (id) DO NOTHING",
                {k: citation.get(k) for k in
                 ("id", "kind", "title", "ref", "snippet", "url",
                  "article_text", "law_uri", "resource_id", "eff_date")},
            )

    # ── fork (참조 모델) ─────────────────────────────────────────────────────
    def fork_thread(self, parent_thread_id: str, fork_point_message_id: str,
                    title: str | None = None) -> str:
        """부모 thread 의 fork_point 에서 분기(C2). 메시지 복사 안 함.

        새 thread.last_seq = fork_point 메시지 seq 로 시드(C2-new) → 조상 prefix(≤cut)와
        자식 메시지(>cut)가 전역 단조. 분기점이 부모 thread 메시지인지 검증.
        """
        new_id = _uuid()
        with self.pool.connection() as conn, conn.transaction():
            row = conn.execute(
                "SELECT seq FROM messages WHERE id = %s AND thread_id = %s",
                (fork_point_message_id, parent_thread_id),
            ).fetchone()
            if row is None:
                raise KeyError(
                    f"fork_point {fork_point_message_id} not in thread {parent_thread_id}")
            cut_seq = row[0]
            conn.execute(
                "INSERT INTO threads (id, title, forked_from_thread_id, "
                " fork_point_message_id, last_seq) VALUES (%s,%s,%s,%s,%s)",
                (new_id, title, parent_thread_id, fork_point_message_id, cut_seq),
            )
        return new_id

    def get_thread_messages(self, thread_id: str) -> list[dict]:
        """fork 가시성 포함 메시지(C2/§3.4): 조상 prefix(≤fork_point) ∪ 자기 메시지, ORDER BY seq."""
        # 각 thread 의 cut(가시 seq 상한): 리프(요청 thread)=NULL(전체), 조상=그 자식이
        # 분기한 메시지 seq. 자식의 fork_point_message_id 는 부모(조상) 안의 메시지를 가리키므로,
        # 조상의 cut 은 **자식(c)의 fork_point** 에서 계산해야 한다(t 자신의 것이 아님).
        sql = """
        WITH RECURSIVE chain AS (
          SELECT id AS thread_id, forked_from_thread_id, fork_point_message_id,
                 NULL::bigint AS cut
            FROM threads WHERE id = %s
          UNION ALL
          SELECT t.id, t.forked_from_thread_id, t.fork_point_message_id,
                 (SELECT seq FROM messages WHERE id = c.fork_point_message_id) AS cut
            FROM threads t JOIN chain c ON t.id = c.forked_from_thread_id
        )
        SELECT m.id::text, m.thread_id::text, m.run_id::text, m.seq, m.parent_id::text, m.role,
               m.content_md, m.tool_name, m.tool_args, m.tool_result,
               m.approval_state, m.model, m.created_at
          FROM messages m JOIN chain c ON m.thread_id = c.thread_id
         WHERE c.cut IS NULL OR m.seq <= c.cut
         ORDER BY m.seq, m.created_at, m.id   -- seq 동률 시 결정적 정렬(계약 위반 방어)
        """
        with self.pool.connection() as conn:
            with conn.cursor(row_factory=dict_row) as cur:
                cur.execute(sql, (thread_id,))
                return cur.fetchall()

    def get_thread_citations(self, thread_id: str) -> list[dict]:
        """이 thread(조상 prefix 포함)가 인용한 distinct citation(M3). 역참조 인덱스 활용."""
        sql = """
        WITH RECURSIVE chain AS (
          SELECT id AS thread_id, forked_from_thread_id, fork_point_message_id,
                 NULL::bigint AS cut
            FROM threads WHERE id = %s
          UNION ALL
          SELECT t.id, t.forked_from_thread_id, t.fork_point_message_id,
                 (SELECT seq FROM messages WHERE id = c.fork_point_message_id)
            FROM threads t JOIN chain c ON t.id = c.forked_from_thread_id
        )
        SELECT DISTINCT ci.id::text, ci.kind, ci.title, ci.ref, ci.snippet, ci.url,
               ci.article_text, ci.law_uri, ci.resource_id, ci.eff_date
          FROM messages m
          JOIN chain c ON m.thread_id = c.thread_id AND (c.cut IS NULL OR m.seq <= c.cut)
          JOIN message_citations mc ON mc.message_id = m.id
          JOIN citations ci ON ci.id = mc.citation_id
        """
        with self.pool.connection() as conn:
            with conn.cursor(row_factory=dict_row) as cur:
                cur.execute(sql, (thread_id,))
                return cur.fetchall()
