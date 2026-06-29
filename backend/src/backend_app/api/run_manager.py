"""RunManager — run↔stream 디커플 + **내구 이벤트로그**(G4 멀티 인스턴스/스케일아웃).

POST 는 run 을 **백그라운드**로 시작하고 run_id 만 즉시 반환한다. run 은 HTTP 연결과 무관하게
서버측에서 완주한다(RunService.run 은 sync 블로킹 → **전용 pump 스레드풀**에서 구동). pump 는 각
이벤트를 **`run_events` 내구 로그(Postgres)에 영속**한다. GET stream 은 그 로그를 run_id 로 poll-tail
한다(seq > Last-Event-ID 부터 replay, 이후 신규를 폴링).

**G4 변경(인프로세스 버퍼 제거)**: 이벤트가 DB 에 있으므로
- **교차 인스턴스**: run 을 시작 안 한 인스턴스도 로그를 읽어 stream 서빙 가능.
- **Last-Event-ID 재연결**: 끊긴 seq 부터 재개(브라우저 EventSource 가 `id:` 로 자동 추적).
- 재시작에도 이벤트 보존(인프로세스 소실 없음).
대가: 라이브 이벤트에 폴링 지연(`RUN_STREAM_POLL_MS`, 기본 150ms) — LLM 에이전트엔 무해.

설계 결정(유지):
- **pump 전용 풀**: POST 즉시반환 계약(`_first_event`/기본 executor 와 분리).
- **terminal 보장**: pump 가 terminal(run.done/error) 없이 끝나면 합성 terminal 을 로그에 영속(stream hang 방지).
  단 **이미 외부 종결(interrupt/sweep)된 run 은 억제**(double-terminal 방지, 교차검증 HIGH).
- 한계(후속): 교차 인스턴스에서 running run 의 협조취소는 best-effort(소유 인스턴스만 취소플래그 보유).
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import logging
import os
import threading
from typing import Any

from backend_app.repositories import ActiveRunExists
from backend_app.services.run_service import RunEvent

# G6: 디커플 경로(백그라운드 pump·유지보수 루프)의 예외 가시화. configure_logging 이 conversation.* 설정.
_log = logging.getLogger("conversation.run_manager")

_TERMINAL = ("run.done", "error")
# 외부 종결(interrupt/sweep/reconcile)로 이미 끝난 run — 합성 terminal 을 억제할 DB 상태.
_TERMINAL_STATUSES = ("completed", "interrupted", "rejected", "error")
_POLL_MS = int(os.environ.get("RUN_STREAM_POLL_MS", "150"))
# liveness(G4): 활성 run heartbeat 주기 / reconcile stale 임계. stale 는 주기의 수 배여야(놓친 heartbeat 허용).
_HEARTBEAT_S = float(os.environ.get("RUN_HEARTBEAT_S", "10"))
_RECONCILE_STALE_S = int(os.environ.get("RUN_RECONCILE_STALE_S", "30"))
# run_events GC(G4 백로그): 종결된 지 RETENTION 지난 run 의 이벤트 삭제. GC 는 저빈도(기본 1h).
_RUN_EVENTS_RETENTION_S = int(os.environ.get("RUN_EVENTS_RETENTION_S", str(7 * 24 * 3600)))  # 7일
_GC_INTERVAL_S = int(os.environ.get("RUN_EVENTS_GC_INTERVAL_S", "3600"))                     # 1시간


def _synthetic_terminal(seq: int) -> RunEvent:
    """terminal 없이 끝났을 때 stream 종료를 위한 합성 error(외부 sweep 등으로 종결된 run)."""
    # FE 계약 §4.2 error 는 {message} 를 읽으므로 message 키 포함(reason 은 부가).
    return RunEvent("error", {"message": "실행이 비정상 종료되었습니다.",
                              "reason": "run ended without terminal event"}, seq)


def _db_terminal_event(run_id: str, status: str, seq: int) -> RunEvent:
    """DB 가 이미 terminal 인데 로그에 terminal 이벤트가 아직(또는 영영) 없을 때, **그 status 를 반영한**
    합성 종료 이벤트. completed/rejected/interrupted → run.done(status), error → error(message).

    (교차검증 LOW): 이전엔 무조건 error 를 방출해, reject/interrupt 직후 빠른 재연결 시 깨끗한 종결을
    "비정상 종료"로 오표시했다. DB 권위 status 를 그대로 비춰 정직하게.
    """
    if status == "error":
        return _synthetic_terminal(seq)
    return RunEvent("run.done", {"run_id": run_id, "status": status}, seq)


class RunManager:
    def __init__(self, run_service: Any, pump_workers: int | None = None) -> None:
        self.rs = run_service
        workers = pump_workers or int(os.environ.get("RUN_PUMP_WORKERS", "64"))
        self._pump_pool = concurrent.futures.ThreadPoolExecutor(
            max_workers=workers, thread_name_prefix="run-pump")
        self._futures: set[concurrent.futures.Future] = set()
        # liveness(G4): 이 인스턴스가 구동 중인 run_id 집합 — maintenance 가 heartbeat 한다.
        self._active: set[str] = set()
        self._stop = threading.Event()
        self._maint = threading.Thread(target=self._maintenance_loop,
                                       name="run-maintenance", daemon=True)
        self._maint.start()

    def _maintenance_loop(self) -> None:
        """주기적으로 (1)활성 run heartbeat (2)stale(소유 워커 사망) run reconcile.

        (1)로 살아있는 run 이 reconcile/timeout 에 안 죽고, (2)로 죽은 워커의 고아가 stale 후 회수된다
        (멀티 인스턴스: 어느 인스턴스든 stale 을 쓸어도 idempotent). repo 없으면(테스트 스텁) 조용히 멈춤."""
        repo = getattr(self.rs, "repo", None)
        if repo is None or not hasattr(repo, "heartbeat_runs"):
            return
        gc_every = max(1, round(_GC_INTERVAL_S / _HEARTBEAT_S))   # heartbeat 몇 틱마다 GC
        tick = 0
        while not self._stop.wait(_HEARTBEAT_S):
            tick += 1
            try:
                repo.heartbeat_runs(list(self._active))
                repo.reconcile_orphan_runs(_RECONCILE_STALE_S)
                # 내구 이벤트로그 GC(저빈도): 종결 오래된 run 의 이벤트 정리(무한누적 방지).
                if tick % gc_every == 0 and hasattr(repo, "gc_run_events"):
                    repo.gc_run_events(_RUN_EVENTS_RETENTION_S)
            except Exception:  # noqa: BLE001 — 유지보수 루프는 일시적 DB 오류에 죽지 않는다(continue)
                # **G6**: 그러나 무로깅이면 heartbeat/reconcile/GC 정지(→ 살아있는 run 오살·이벤트 무한증식)가
                # 영영 불가시였다. 진짜 예외를 로깅하고 루프는 계속(다음 틱 재시도).
                _log.exception("maintenance loop error (tick=%s) — continuing", tick)

    # ── 시작/재개(백그라운드) ────────────────────────────────────────────────
    async def start(self, thread_id: str, message: str, model: str | None = None) -> str:
        """run 을 백그라운드로 시작하고 run_id 반환. 동시 run 이면 ActiveRunExists(→409). model=선택 LLM."""
        loop = asyncio.get_running_loop()
        gen = self.rs.run(message, thread_id, model)
        # 첫 이벤트(run.started)를 당겨 run_id 확보 + open_run 의 ActiveRunExists 를 동기 표면화(409).
        first = await loop.run_in_executor(None, _first_event, gen)
        run_id = first.data["run_id"]
        await loop.run_in_executor(None, self._persist, thread_id, run_id, first)
        self._spawn_pump(gen, loop, thread_id, run_id)
        return run_id

    async def resume(self, thread_id: str, approve: bool,
                     approved_ids: list[str] | None = None) -> str:
        """승인/거절(또는 per-tool 선택적 실행)을 백그라운드로 재개. 같은 run_id 로그에 이어 영속."""
        active = self.rs.repo.get_active_run(thread_id)
        if active is None or active[1] != "awaiting_approval":
            raise ValueError("재개할 승인-대기 run 이 없습니다")
        run_id = active[0]
        loop = asyncio.get_running_loop()
        self._spawn_pump(self.rs.resume(thread_id, approve, approved_ids), loop, thread_id, run_id)
        return run_id

    def _persist(self, thread_id: str, run_id: str, ev: RunEvent) -> None:
        self.rs.repo.append_run_event(thread_id, run_id, ev.seq, ev.event, ev.data)

    def _spawn_pump(self, gen: Any, loop: asyncio.AbstractEventLoop,
                    thread_id: str, run_id: str) -> None:
        """gen 을 전용 pump 스레드에서 끝까지 소비하며 각 이벤트를 내구 로그에 영속(연결 무관 완주)."""
        repo = self.rs.repo
        self._active.add(run_id)               # liveness: maintenance 가 이 run 을 heartbeat

        def pump() -> None:
            saw_terminal = False
            last_name = None
            try:
                for ev in gen:
                    last_name = ev.event
                    if ev.event in _TERMINAL:
                        saw_terminal = True
                    repo.append_run_event(thread_id, run_id, ev.seq, ev.event, ev.data)
            except Exception:  # noqa: BLE001 — RunService 내부가 error 처리. 밖 예외는 finally 가 terminal 보장
                # **G6**: pump 밖 예외(append_run_event PG 실패 등)는 무로깅이면 run 이 왜 멈췄는지(이벤트
                # 미영속) 디커플 경로라 진단 불가였다. 로깅하고 finally 가 합성 terminal 로 stream hang 방지.
                _log.exception("pump error run_id=%s thread=%s last=%s", run_id, thread_id, last_name)
            finally:
                self._active.discard(run_id)   # 종료/일시정지 → heartbeat 중단(stale 시 reconcile 회수)
                # terminal 없이 끝났고 승인 일시정지(approval.requested)도 아닐 때만 합성.
                # **이미 외부 종결(interrupt/sweep)된 run 은 억제**(double-terminal 방지). DB 가 권위.
                if not saw_terminal and last_name != "approval.requested":
                    try:
                        run = repo.get_run(run_id)
                    except Exception:  # noqa: BLE001 — 확인불가면 안전하게 합성(hang 방지 우선)
                        run = None
                    if run is None or run[1] not in _TERMINAL_STATUSES:
                        seq = repo.next_seq(thread_id)
                        ev = _synthetic_terminal(seq)
                        repo.append_run_event(thread_id, run_id, seq, ev.event, ev.data)

        fut = self._pump_pool.submit(pump)
        self._futures.add(fut)
        fut.add_done_callback(self._futures.discard)

    # ── 중지(interrupt) ──────────────────────────────────────────────────────
    async def interrupt(self, run_id: str) -> dict:
        """run 중지. running=협조취소(소유 인스턴스 pump 가 다음 청크서 terminal 영속),
        awaiting_approval/고아=DB 직접 종결+terminal 영속. 없으면 KeyError(404), 종료됨/패자 ValueError(409)."""
        run = self.rs.repo.get_run(run_id)
        if run is None:
            raise KeyError(run_id)
        thread_id, status = run
        if status == "running":
            if self.rs.request_cancel(run_id):
                return {"run_id": run_id, "status": "interrupting"}   # 소유 pump 가 곧 terminal 영속
            # 로컬 pump 가 없는 running(고아·타 인스턴스) → DB 직접 종결 + terminal 영속(best-effort).
            if self.rs.repo.try_transition(run_id, "running", "interrupted", ended=True):
                self._push_terminal(run_id, thread_id)
                return {"run_id": run_id, "status": "interrupted"}
            raise ValueError("중지할 수 없습니다(이미 종료됨)")
        if status == "awaiting_approval":
            if self.rs.interrupt_paused(thread_id, run_id):
                self._push_terminal(run_id, thread_id)
                return {"run_id": run_id, "status": "interrupted"}
            raise ValueError("이미 처리된 run 입니다")
        raise ValueError(f"이미 종료된 run 입니다({status})")

    def _push_terminal(self, run_id: str, thread_id: str) -> None:
        """일시정지/고아 run 중지 시 도는 루프가 없으므로 종료 이벤트를 로그에 직접 영속(stream 종료 보장)."""
        seq = self.rs.repo.next_seq(thread_id)
        self.rs.repo.append_run_event(thread_id, run_id, seq, "run.done",
                                      {"run_id": run_id, "status": "interrupted"})

    def shutdown(self, timeout: float = 10.0) -> None:
        """lifespan 종료: maintenance 정지 + in-flight pump best-effort 대기 후 풀 종료(고아·닫힌풀 방지)."""
        self._stop.set()                       # maintenance 루프 종료 신호
        concurrent.futures.wait(list(self._futures), timeout=timeout)
        self._pump_pool.shutdown(wait=False)

    # ── 스트림(내구 로그 poll-tail) ──────────────────────────────────────────
    async def stream(self, run_id: str, last_event_id: int = -1):
        """run_id 이벤트를 내구 로그에서 replay+poll 로 흘린다. terminal 에서 종료.

        last_event_id(=마지막 수신 seq) 이후부터 — Last-Event-ID 재연결·교차 인스턴스. 승인 대기에서는
        종료 않고 resume 이벤트를 계속 폴링. TTL sweep 등으로 terminal 이벤트 없이 종결된 run 은 합성 종료.
        클라가 끊으면 이 제너레이터만 중단되고 백그라운드 run 은 영향 없음.
        """
        loop = asyncio.get_running_loop()
        repo = self.rs.repo
        cursor = last_event_id
        while True:
            rows = await loop.run_in_executor(None, repo.get_run_events_after, run_id, cursor)
            for r in rows:
                yield RunEvent(r["event"], r["data"], r["seq"])
                cursor = r["seq"]
                if r["event"] in _TERMINAL:
                    return
            if not rows:
                # 신규 이벤트 없음 — run 이 외부 종결(TTL sweep)됐거나, terminal 이벤트가 아직 영속 전인
                # 빠른 재연결. DB status 를 **반영한** 종료 이벤트로 마무리(reject/interrupt 를 error 로
                # 오표시하던 LOW 결함 수정). 영속 안 함(권위는 곧 영속될/이미 있는 실제 이벤트).
                run = await loop.run_in_executor(None, repo.get_run, run_id)
                if run is not None and run[1] in _TERMINAL_STATUSES:
                    yield _db_terminal_event(run_id, run[1], cursor + 1)
                    return
                await asyncio.sleep(_POLL_MS / 1000)


def _first_event(gen: Any) -> Any:
    """gen 의 첫 이벤트(run.started)를 당긴다. open_run 의 ActiveRunExists 는 전파."""
    try:
        return next(gen)
    except ActiveRunExists:
        raise
