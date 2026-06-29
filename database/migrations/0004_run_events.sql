-- 0004: run_events — SSE 이벤트 내구 로그 (G4 멀티 인스턴스/스케일아웃).
--
-- 기존 stream 버퍼는 **인프로세스**(RunManager.buffers)라 (a)run 을 시작한 인스턴스에서만 stream 가능,
-- (b)재시작 시 소실(Last-Event-ID 재연결 불가)이었다. 이벤트를 DB 에 영속하면:
--   · 어느 인스턴스든 run_id 로 poll-tail → 교차 인스턴스 stream
--   · Last-Event-ID(=seq) 로 끊긴 지점부터 재연결
-- seq 는 thread 전역 단조 카운터(threads.last_seq) — 이벤트 data 의 seq 와 동일(FE dedup/정렬과 단일 커서).
-- 한 seq 는 전역 유일하게 한 이벤트에 배정되므로 (run_id, seq) PK 안전. 정렬·재연결 커서 = seq.

CREATE TABLE IF NOT EXISTS run_events (
  thread_id  UUID   NOT NULL,
  run_id     UUID   NOT NULL,
  seq        BIGINT NOT NULL,
  event      TEXT   NOT NULL,
  data       JSONB  NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (run_id, seq)
);

-- stream tail: run_id 의 seq > last 를 순서대로.
CREATE INDEX IF NOT EXISTS run_events_run_seq_idx ON run_events (run_id, seq);
