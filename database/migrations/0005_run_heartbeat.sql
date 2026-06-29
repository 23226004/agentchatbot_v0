-- 0005: runs.heartbeat_at — liveness 인지 reconcile (G4 멀티 인스턴스).
--
-- 기존 reconcile 은 부팅 시 **모든** running run 을 error 로 쓸어 멀티워커/롤링재시작서 살아있는 워커의
-- run 까지 죽였다(교차검증 HIGH). 각 인스턴스가 자기 활성 run 의 heartbeat_at 을 주기 갱신하고,
-- reconcile 은 heartbeat 가 stale(소유 워커 사망)한 run 만 쓸도록 바꾼다 → 살아있는 워커 run 보존.
-- 부수: timeout_stale_runs 도 started_at 대신 heartbeat 기반으로 → 긴 정상 run 오살 방지(G5).

ALTER TABLE runs ADD COLUMN IF NOT EXISTS heartbeat_at TIMESTAMPTZ NOT NULL DEFAULT now();

-- reconcile/timeout sweep 대상 조회용(status + heartbeat).
CREATE INDEX IF NOT EXISTS runs_status_heartbeat_idx ON runs (status, heartbeat_at);
