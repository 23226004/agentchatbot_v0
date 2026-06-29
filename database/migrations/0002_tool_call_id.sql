-- 슬라이스4: 도구셀 메시지에 LangChain tool_call_id 영속.
-- 승인 흐름에서 tool.call(run 단계)과 tool.result(resume 단계)가 다른 호출에 걸쳐 일어나
-- in-memory 상관이 불가 → (run_id, tool_call_id)로 결과를 교차호출 갱신하기 위함.
ALTER TABLE messages ADD COLUMN IF NOT EXISTS tool_call_id TEXT;
CREATE INDEX IF NOT EXISTS messages_run_toolcall_idx
  ON messages (run_id, tool_call_id) WHERE tool_call_id IS NOT NULL;
