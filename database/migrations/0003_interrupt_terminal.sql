-- 0003: 사용자 중지(interrupt) — 'interrupted' 를 **터미널** 상태로 확정.
--
-- run_status enum 의 'interrupted' 는 0001 에서 예약만 되고 어디서도 SET 되지 않았다(미사용).
-- 이제 POST /runs/{id}/interrupt 가 running/awaiting_approval run 을 'interrupted'(종료)로 전이한다.
-- 터미널이므로 **활성 run 유니크 인덱스에서 제외**해야 중지 후 같은 thread 에 새 run 을 시작할 수 있다
-- (제외 안 하면 종료된 interrupted run 이 thread 를 영구 잠금). running·awaiting_approval 만 활성.
--
-- 멱등: DROP + CREATE 로 매 부팅 정확한 술어로 재생성(부팅은 서빙 전 단일 스레드라 안전).

DROP INDEX IF EXISTS one_active_run_per_thread;
CREATE UNIQUE INDEX IF NOT EXISTS one_active_run_per_thread ON runs (thread_id)
  WHERE status IN ('running', 'awaiting_approval');
