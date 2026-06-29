-- conversation-store transcript 스키마 (Design v0.3 §3)
-- public schema — LangGraph checkpoint* 4테이블(checkpoints/checkpoint_blobs/
-- checkpoint_writes/checkpoint_migrations)과 이름 비충돌 → 같은 DB·schema 공존(§6 T1).
-- 멱등 재실행 가능(enum=DO 가드, table=IF NOT EXISTS).

-- ── 3.1 enum ──────────────────────────────────────────────────────────────
DO $$ BEGIN
  CREATE TYPE message_role AS ENUM ('user','agent','tool');
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

DO $$ BEGIN
  CREATE TYPE run_status AS ENUM
    ('running','awaiting_approval','interrupted','completed','rejected','error');
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

-- ── 3.2 transcript ────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS threads (
  id UUID PRIMARY KEY,
  title TEXT,
  owner_id UUID,
  forked_from_thread_id UUID REFERENCES threads(id),  -- C2 참조 분기
  fork_point_message_id UUID,                          -- 분기 기준(부모 thread 메시지)
  last_seq BIGINT NOT NULL DEFAULT 0,                  -- M1 원자 seq 카운터
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS runs (
  id UUID PRIMARY KEY,
  thread_id UUID NOT NULL REFERENCES threads(id),
  status run_status NOT NULL DEFAULT 'running',
  checkpoint_id TEXT,
  checkpoint_ns TEXT,
  started_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  ended_at TIMESTAMPTZ
);
-- M1: thread당 활성 run 1개 DB강제(동시 run 금지) → seq 단일 writer 보증(409 근거)
CREATE UNIQUE INDEX IF NOT EXISTS one_active_run_per_thread ON runs (thread_id)
  WHERE status IN ('running','awaiting_approval','interrupted');

CREATE TABLE IF NOT EXISTS messages (
  id UUID PRIMARY KEY,
  thread_id UUID NOT NULL REFERENCES threads(id),
  run_id UUID REFERENCES runs(id),
  seq BIGINT NOT NULL,                                 -- 순서의 정본(thread 내 단조)
  parent_id UUID REFERENCES messages(id),              -- 턴 내 셀 구조(분기 아님)
  role message_role NOT NULL,
  content_md TEXT,                                     -- markdown([[cite:id]] 포함, 최종본문)
  tool_name TEXT,
  tool_args JSONB,
  tool_result JSONB,
  approval_state TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  CHECK (parent_id IS NULL OR parent_id <> id),
  UNIQUE (thread_id, seq)                              -- 단조·유일
);
CREATE INDEX IF NOT EXISTS messages_thread_seq_idx ON messages (thread_id, seq);
CREATE INDEX IF NOT EXISTS threads_owner_updated_idx ON threads (owner_id, updated_at);

-- C1/A: 전역 불변 스냅샷(조문-시행본 1행). id 결정적이라 thread간 공유, 전문 동결.
CREATE TABLE IF NOT EXISTS citations (
  id UUID PRIMARY KEY,                                 -- = db-layer UUIDv5(article uri)
  kind TEXT, title TEXT, ref TEXT, snippet TEXT, url TEXT,
  article_text TEXT,                                   -- A: 조문 전문 동결(법적 무결성)
  law_uri TEXT, resource_id TEXT, eff_date DATE,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
-- C1: message↔citation 1:N(어느 답변이 무엇을 인용)
CREATE TABLE IF NOT EXISTS message_citations (
  message_id UUID NOT NULL REFERENCES messages(id),
  citation_id UUID NOT NULL REFERENCES citations(id),
  PRIMARY KEY (message_id, citation_id)
);
CREATE INDEX IF NOT EXISTS message_citations_citation_idx
  ON message_citations (citation_id);                  -- M3: citation→역참조

CREATE TABLE IF NOT EXISTS summaries (
  id UUID PRIMARY KEY,
  thread_id UUID NOT NULL REFERENCES threads(id),
  covers_from_seq BIGINT, covers_to_seq BIGINT,
  content_md TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS settings (
  scope TEXT PRIMARY KEY,
  model TEXT, server_url TEXT, theme TEXT,
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
