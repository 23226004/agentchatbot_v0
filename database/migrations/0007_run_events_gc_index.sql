-- 0007: runs.ended_at 부분 인덱스 — run_events GC(종결 오래된 run 의 이벤트 일괄삭제) 효율.
--
-- run_events 는 SSE 재연결용 내구 로그라 무한 누적되면 안 된다(G4 백로그). GC 는 "종결(ended_at NOT NULL)
-- 된 지 보존기간 지난 run" 의 이벤트만 지운다 → 그 대상을 찾는 `WHERE ended_at < cutoff` 를 빠르게.
-- 활성 run(ended_at NULL)은 인덱스서 제외(부분 인덱스).

CREATE INDEX IF NOT EXISTS runs_ended_at_idx ON runs (ended_at) WHERE ended_at IS NOT NULL;
