"""ConversationRepository 통합테스트 (Design §10). 라이브 Postgres 필요 — 미가동 시 skip.

기동: docker compose -f deploy/docker-compose.convstore.yml up -d
      DATABASE_URL=postgresql://convstore:convstore@localhost:5434/convstore
"""

from __future__ import annotations

import concurrent.futures
import uuid

import pytest

psycopg = pytest.importorskip("psycopg")
pytest.importorskip("psycopg_pool")

from backend_app.db import build_pool, run_migrations  # noqa: E402
from backend_app.repositories import ActiveRunExists, ConversationRepository  # noqa: E402


@pytest.fixture(scope="session")
def pool():
    try:
        p = build_pool()
        run_migrations(p)
    except Exception as exc:  # noqa: BLE001 — 인프라 미가동 시 통합테스트 skip(db-layer 패턴)
        pytest.skip(f"convstore postgres 미가동: {exc}")
    yield p
    p.close()


@pytest.fixture
def repo(pool):
    return ConversationRepository(pool)


def _cit(cid, text):
    return {"id": cid, "kind": "law", "title": "건축법", "ref": "건축법 제2조",
            "snippet": text[:200], "url": "u", "article_text": text,
            "law_uri": "uri", "resource_id": "001823", "eff_date": "2026-02-27"}


def test_seq_monotonic(repo):
    t = repo.create_thread("t")
    assert [repo.next_seq(t) for _ in range(3)] == [1, 2, 3]


def test_seq_unique_under_concurrency(repo):
    t = repo.create_thread("c")
    n = 30
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as ex:
        seqs = list(ex.map(lambda _: repo.next_seq(t), range(n)))
    assert sorted(seqs) == list(range(1, n + 1))   # 단조·유일·무손실(원자 채번)


def test_active_run_409_then_reopen(repo):
    t = repo.create_thread("r")
    r1 = repo.open_run(t)
    with pytest.raises(ActiveRunExists):
        repo.open_run(t)                            # 동시 run 금지(partial unique index)
    repo.set_run_status(r1, "completed", ended=True)
    repo.open_run(t)                                # 종료 후 재개방 가능


def test_reconcile_orphan_runs_releases_lock(repo):
    """G4 liveness: heartbeat stale(소유 워커 死)한 running → error·인덱스 해제. fresh(살아있는) run 은 보존."""
    t = repo.create_thread("orphan")
    repo.open_run(t)                                # running, heartbeat=now()
    # 살아있는 워커 가정(fresh heartbeat) → reconcile(grace)은 안 죽임(멀티워커 오살 방지)
    assert repo.reconcile_orphan_runs(stale_seconds=30) == 0
    assert repo.get_active_run(t)[1] == "running"
    # 죽은 워커 가정(stale) → 회수
    assert repo.reconcile_orphan_runs(stale_seconds=0) >= 1
    repo.open_run(t)                                # 인덱스 해제되어 새 run 개방 가능


def test_reconcile_preserves_awaiting_approval(repo):
    """교차검증 실버그 회귀: reconcile 은 awaiting_approval(승인대기·durable)을 죽이면 안 된다."""
    t = repo.create_thread("await-survive")
    r = repo.open_run(t)
    repo.set_run_status(r, "awaiting_approval")
    repo.reconcile_orphan_runs()                    # 부팅 reconcile
    assert repo.get_active_run(t) == (r, "awaiting_approval")   # 살아남아 재개 가능
    with pytest.raises(ActiveRunExists):
        repo.open_run(t)                            # 여전히 활성(인덱스 보호)


def test_try_transition_cas_single_winner(repo):
    """동시 resume 보호: from_status 일 때만 전이, 1명만 성공."""
    t = repo.create_thread("cas")
    r = repo.open_run(t)
    repo.set_run_status(r, "awaiting_approval")
    assert repo.try_transition(r, "awaiting_approval", "running") is True
    # 이미 running → 같은 전이 재시도는 실패(패자)
    assert repo.try_transition(r, "awaiting_approval", "running") is False


def test_try_transition_concurrent_single_winner(repo):
    """진짜 동시 resume: N개 스레드가 awaiting→running CAS → 정확히 1명만 성공(PG row-lock).

    무가드 UPDATE 면 전원 통과(이중 drive). CAS(WHERE status=from)가 단일 winner 보장.
    """
    import threading

    t = repo.create_thread("cas-conc")
    r = repo.open_run(t)
    repo.set_run_status(r, "awaiting_approval")
    n = 16
    barrier = threading.Barrier(n)

    def _claim(_):
        barrier.wait()                              # 동시 출발
        return repo.try_transition(r, "awaiting_approval", "running")

    with concurrent.futures.ThreadPoolExecutor(max_workers=n) as ex:
        wins = list(ex.map(_claim, range(n)))
    assert sum(wins) == 1                            # 정확히 1명만 awaiting 을 소비


def test_timeout_stale_approvals(repo):
    """승인 무한대기 방지(§8): TTL 초과 awaiting 만 error, 최근 awaiting 은 보존(특정 run 기준)."""
    t_old = repo.create_thread("ttl-old")
    r_old = repo.open_run(t_old)
    repo.set_run_status(r_old, "awaiting_approval")
    with repo.pool.connection() as conn:           # started_at 을 과거로(1시간 전) 강제
        conn.execute("UPDATE runs SET started_at = now() - interval '1 hour' WHERE id=%s", (r_old,))
    t_new = repo.create_thread("ttl-fresh")
    r_new = repo.open_run(t_new)
    repo.set_run_status(r_new, "awaiting_approval")  # 방금 → 보존돼야
    repo.timeout_stale_approvals(60)
    with repo.pool.connection() as conn:
        old_st = conn.execute("SELECT status::text FROM runs WHERE id=%s", (r_old,)).fetchone()[0]
        new_st = conn.execute("SELECT status::text FROM runs WHERE id=%s", (r_new,)).fetchone()[0]
    assert old_st == "error"                        # TTL 초과 → error
    assert new_st == "awaiting_approval"            # 최근 → 보존
    repo.open_run(t_old)                            # 만료 run 종결로 잠금 해제 확인


def test_timeout_stale_runs_reaps_disconnect_orphans(repo):
    """고아(running) 회수: **heartbeat stale**(소유 워커 死)한 running 만 error, fresh 는 보존(G4/G5).

    heartbeat 기반이라 긴 정상 run 도 heartbeat 가 fresh 면 안 죽는다(started_at 기준의 장기 run 오살 해소)."""
    t_old = repo.create_thread("run-old")
    r_old = repo.open_run(t_old)                    # heartbeat 갱신 끊김(소유 워커 死) 가정
    with repo.pool.connection() as conn:
        conn.execute("UPDATE runs SET heartbeat_at = now() - interval '1 hour' WHERE id=%s", (r_old,))
    t_new = repo.create_thread("run-fresh")
    r_new = repo.open_run(t_new)                    # heartbeat=now()(살아있는 워커) → 보존
    repo.timeout_stale_runs(60)
    with repo.pool.connection() as conn:
        old_st = conn.execute("SELECT status::text FROM runs WHERE id=%s", (r_old,)).fetchone()[0]
        new_st = conn.execute("SELECT status::text FROM runs WHERE id=%s", (r_new,)).fetchone()[0]
    assert old_st == "error" and new_st == "running"
    repo.open_run(t_old)                            # 고아 회수로 thread 잠금 해제


def test_citation_freeze_is_immutable(repo, pool):
    cid = str(uuid.uuid4())
    repo.freeze_citation(_cit(cid, "원본 전문 A"))
    repo.freeze_citation(_cit(cid, "변조 전문 B"))   # 같은 id 재동결 → 무시(ON CONFLICT DO NOTHING)
    with pool.connection() as conn:
        text = conn.execute("SELECT article_text FROM citations WHERE id=%s", (cid,)).fetchone()[0]
    assert text == "원본 전문 A"                     # 첫-동결 권위(법적 무결성)


def test_message_citations_1n_distinct(repo):
    t = repo.create_thread("mc")
    c1, c2 = str(uuid.uuid4()), str(uuid.uuid4())
    repo.freeze_citation(_cit(c1, "조문1")); repo.freeze_citation(_cit(c2, "조문2"))
    repo.add_message(t, role="agent", content_md="답1 [[cite]]", citation_ids=[c1, c2])
    repo.add_message(t, role="agent", content_md="답2 [[cite]]", citation_ids=[c1])  # 공유 재인용
    cits = repo.get_thread_citations(t)
    assert {str(c["id"]) for c in cits} == {c1, c2}  # 전체=distinct(전역 dedup)


def test_fork_ancestor_union_visibility(repo):
    parent = repo.create_thread("p")
    m1, _ = repo.add_message(parent, role="user", content_md="질문1")
    m2, _ = repo.add_message(parent, role="agent", content_md="답1")
    m3, _ = repo.add_message(parent, role="user", content_md="질문2(분기 이후)")
    child = repo.fork_thread(parent, fork_point_message_id=m2)   # m2(seq2)에서 분기
    cm, cseq = repo.add_message(child, role="agent", content_md="자식 답")
    assert cseq == 3                                 # fork seq 연속성(부모 fork_point seq=2 시드 → +1)
    msgs = repo.get_thread_messages(child)
    contents = [m["content_md"] for m in msgs]
    assert contents == ["질문1", "답1", "자식 답"]    # 조상 prefix(≤2) + 자식, m3(분기후)는 제외, ORDER BY seq


def test_heartbeat_protects_run_from_reconcile(repo):
    """G4 liveness: heartbeat_runs 로 갱신된 running 은 stale 기준 reconcile/timeout 에 안 죽는다."""
    t = repo.create_thread("hb")
    r = repo.open_run(t)
    with repo.pool.connection() as conn:                        # 워커 死처럼 heartbeat 과거로
        conn.execute("UPDATE runs SET heartbeat_at = now() - interval '1 hour' WHERE id=%s", (r,))
    assert repo.heartbeat_runs([r]) == 1                        # 살아있는 워커가 heartbeat 갱신
    assert repo.reconcile_orphan_runs(stale_seconds=30) == 0    # 더는 stale 아님 → 보존
    assert repo.get_active_run(t)[1] == "running"
    # heartbeat_runs 는 running 만 — 터미널/승인대기엔 무영향
    repo.set_run_status(r, "awaiting_approval")
    assert repo.heartbeat_runs([r]) == 0


def test_gc_run_events_deletes_old_terminal_keeps_active_and_recent(repo):
    """run_events GC: 종결된 지 오래된 run 의 이벤트만 삭제. 활성·최근 종결 run 은 보존(transcript 무관)."""
    # 오래 전 종결 run
    t1 = repo.create_thread("gc-old")
    r_old = repo.open_run(t1); repo.set_run_status(r_old, "completed", ended=True)
    repo.append_run_event(t1, r_old, repo.next_seq(t1), "run.started", {"run_id": r_old})
    repo.append_run_event(t1, r_old, repo.next_seq(t1), "run.done", {"run_id": r_old})
    with repo.pool.connection() as conn:
        conn.execute("UPDATE runs SET ended_at = now() - interval '30 days' WHERE id=%s", (r_old,))
    # 최근 종결 run
    t2 = repo.create_thread("gc-recent")
    r_new = repo.open_run(t2); repo.set_run_status(r_new, "completed", ended=True)
    repo.append_run_event(t2, r_new, repo.next_seq(t2), "run.started", {"run_id": r_new})
    # 활성 run (ended_at NULL)
    t3 = repo.create_thread("gc-active")
    r_act = repo.open_run(t3)
    repo.append_run_event(t3, r_act, repo.next_seq(t3), "run.started", {"run_id": r_act})

    assert len(repo.get_run_events_after(r_old, -1)) == 2          # 삭제 전
    repo.gc_run_events(7 * 24 * 3600)                              # 7일 보존

    assert repo.get_run_events_after(r_old, -1) == []             # 오래된 종결 → 삭제
    assert len(repo.get_run_events_after(r_new, -1)) == 1         # 최근 종결 → 보존
    assert len(repo.get_run_events_after(r_act, -1)) == 1         # 활성(ended_at NULL) → 보존
    # transcript(messages)는 GC 무관 — run_events 만 정리


def test_build_pool_fails_fast_on_bad_db_no_secret_leak():
    """교차검증: 잘못된 DATABASE_URL/PG 미가동 → 30s PoolTimeout 대신 즉시 명확한 RuntimeError, 비밀 비노출."""
    import time
    from backend_app.db import build_pool
    t0 = time.time()
    with pytest.raises(RuntimeError) as ei:
        build_pool("postgresql://convstore:s3cretpw@localhost:59999/nope")
    assert time.time() - t0 < 8.0                    # 빠른 실패(백그라운드 재시도 30s 아님)
    assert "s3cretpw" not in str(ei.value)           # 비밀번호 미노출


def test_db_connect_timeout_empty_does_not_crash(monkeypatch):
    """DB_CONNECT_TIMEOUT='' (빈값)이 int('') 부팅 크래시 안 내고 기본(5)으로 — 정상 DB 면 부팅 OK."""
    from backend_app.db import build_pool
    monkeypatch.setenv("DB_CONNECT_TIMEOUT", "")
    p = build_pool(); p.close()       # 정상 convstore — 빈값이 5 로 폴백, 크래시 0
