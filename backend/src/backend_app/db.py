
"""PostgreSQL 연결 풀 + 마이그레이션 실행 (Design §6).

장수명 `psycopg_pool.ConnectionPool` 1개를 만들어 DI로 주입한다(`from_conn_string` 금지).
transcript 스키마와 LangGraph checkpoint* 테이블이 같은 DB에 공존한다.
"""

from __future__ import annotations

import os
from pathlib import Path

import psycopg
from psycopg_pool import ConnectionPool

# 스키마 마이그레이션(번호순, 멱등 DDL).
_MIGRATIONS = ["0001_conversation_store.sql", "0002_tool_call_id.sql",
               "0003_interrupt_terminal.sql", "0004_run_events.sql",
               "0005_run_heartbeat.sql", "0006_run_model.sql",
               "0007_run_events_gc_index.sql"]


def database_url() -> str:
    return os.environ.get(
        "DATABASE_URL", "postgresql://convstore:convstore@localhost:5434/convstore"
    )


def build_pool(conninfo: str | None = None, *,
               min_size: int | None = None, max_size: int | None = None) -> ConnectionPool:
    """장수명 연결 풀. 애플리케이션 수명 동안 1개 유지(합성 루트에서 생성·주입).

    **autocommit=True 필수**: LangGraph PostgresSaver.setup() 의 `CREATE INDEX CONCURRENTLY`
    가 트랜잭션 밖에서만 실행 가능하기 때문(같은 풀 공유, §6 T1). ConversationRepository 의
    원자 메서드는 명시적 `with conn.transaction():` 로 BEGIN/COMMIT 하므로 autocommit 과 양립.

    **풀 크기**: pump 전용 풀(RUN_PUMP_WORKERS=64)이 동시 다수 run 을 구동하며 이 풀(repo+checkpointer
    공유)을 경합한다. 기본 max_size=8 은 비대칭이 커, 원격 DB(획득마다 RTT)+느린 LLM+고동시성에서
    풀이 장시간 포화→30s timeout 초과분이 **조용한 run error** 가 되는 부하 리스크였다(교차검증 Medium).
    `DB_POOL_MAX`(기본 20)·`DB_POOL_MIN`(기본 2)으로 운영서 튜닝(PG max_connections·checkpointer 공유 고려).
    """
    if min_size is None:
        min_size = int(os.environ.get("DB_POOL_MIN", "2"))
    if max_size is None:
        max_size = int(os.environ.get("DB_POOL_MAX", "20"))
    ci = conninfo or database_url()
    # **fail-fast 연결 검증**(교차검증 Medium): 풀은 잘못된 DB 에 즉시 안 깨지고 백그라운드 재시도라,
    # DATABASE_URL 오타·PG 미가동 시 30s 뒤 모호한 PoolTimeout 으로 죽었다(진짜 원인은 재시도 로그에 묻힘).
    # 직접 connect 로 **즉시 명확한 원인**(connection refused / database does not exist)을 부팅서 표면화.
    # psycopg 예외 메시지는 host/port/dbname 만 담고 비밀번호는 안 담는다(누출 없음).
    # connect_timeout 파싱은 try **밖**에서(비숫자/빈값이 "DB 연결 실패" 로 오진단되지 않게). 0/음수는
    # libpq 에서 '무한대기'라 fail-fast 를 무력화 → 최소 1초로 클램프(교차검증 LOW footgun).
    _ct = os.environ.get("DB_CONNECT_TIMEOUT", "5").strip()
    connect_timeout = max(1, int(_ct)) if _ct else 5
    try:
        psycopg.connect(ci, connect_timeout=connect_timeout).close()
    except Exception as exc:  # noqa: BLE001 — 부팅 시 DB 도달 불가는 명시적 실패로(모호 PoolTimeout 대신)
        raise RuntimeError(f"DB 연결 실패 — DATABASE_URL/PG 가동 확인 필요: {exc}") from exc
    pool = ConnectionPool(ci, min_size=min_size, max_size=max_size,
                          kwargs={"autocommit": True}, open=False)
    pool.open()
    return pool


def _migrations_dir() -> Path:
    """마이그레이션 SQL 디렉터리 해소. **설치 레이아웃 무관 견고화**(컨테이너/휠 배포 대비):
    1) `MIGRATIONS_DIR` env — 명시 override(이미지가 SQL 을 패키지 밖 경로에 둘 때. Docker 가 설정).
    2) repo 레이아웃 `repo_root/database/migrations`(editable/dev — 존재할 때만).
    어느 쪽도 없으면 **명확히 실패**(silent no-op 금지 — 빈 경로로 진행해 'relation 없음' 으로 늦게 터지지 않게).
    (과거: repo-상대 경로만 → 비편집 설치 시 `…/lib/database/migrations` 오해소·FileNotFound. 교차검증 적발.)"""
    env = os.environ.get("MIGRATIONS_DIR")
    if env:
        d = Path(env)
        if not d.is_dir():
            raise RuntimeError(f"MIGRATIONS_DIR='{env}' 디렉터리가 없음 — 마이그레이션 SQL 경로 확인")
        return d
    repo = Path(__file__).resolve().parents[3] / "database" / "migrations"
    if repo.is_dir():
        return repo
    raise RuntimeError(
        "마이그레이션 SQL 디렉터리를 찾지 못함 — 비-repo 설치(휠/컨테이너)면 `MIGRATIONS_DIR` 를 "
        "SQL 경로로 설정하라(예: Docker 가 database/migrations 를 동봉 후 MIGRATIONS_DIR 지정)."
    )


def run_migrations(pool: ConnectionPool) -> list[str]:
    """database/migrations/*.sql 을 순서대로 실행(멱등 DDL). 적용한 파일명 반환."""
    applied: list[str] = []
    mdir = _migrations_dir()
    with pool.connection() as conn:
        for name in _MIGRATIONS:
            sql = (mdir / name).read_text(encoding="utf-8")
            conn.execute(sql)
            applied.append(name)
        conn.commit()
    return applied
