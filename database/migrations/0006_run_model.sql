-- 0006: 문답에 사용된 LLM 모델 기록 (런타임 모델 선택 — GPT/로컬).
--
-- 어떤 모델로 답했는지 채팅 DB 에 남겨 (a)이력 화면서 답변별 모델 표시, (b)감사/재현/품질분석에 쓴다.
-- runs.model = 그 턴(run)에 선택된 모델, messages.model = 그 메시지를 생성한 모델(agent 답변).
-- nullable: 기능 도입 전 기존 행·user/tool 메시지는 NULL.

ALTER TABLE runs ADD COLUMN IF NOT EXISTS model TEXT;
ALTER TABLE messages ADD COLUMN IF NOT EXISTS model TEXT;
