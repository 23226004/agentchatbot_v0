# conversation-store Design Document

> **Summary**: 사용자 채팅 영속 — PostgreSQL transcript(스레드·메시지·분기·근거·요약) + LangGraph PostgresSaver(실행상태). RunService가 agent updates를 FE SSE 8종으로 변환하며 단일 지점에서 영속·seq 부여. **RunService = db-layer와 conversation-store의 단일 권위 소스.**
>
> **Project**: 2026_06_20_Agent · **Version**: 0.3 · **Author**: TreeAnderson · **Date**: 2026-06-25 · **Status**: Do-ready (2차 3관점 교차검증 반영)
> **Plan**: [conversation-store.plan.md](../../01-plan/features/conversation-store.plan.md) (v0.2)

### v0.3 개정 (2차 3관점 교차검증 — v0.2 파생 모순 수정, 전부 fork↔checkpoint↔seq↔dual-body 수렴)
| 코드 | 해소 | 검증자 | 위치 |
|------|------|--------|------|
| C2-new | **fork seq 연속성** — 자식 last_seq=부모 fork_point seq 시드 → 전역 단조, `ORDER BY seq` 단일정렬 정확 | 데이터모델 | §3.3·§3.4 |
| M2-new/M2-a | **reconcile=transcript 권위** — checkpoint는 resume 전용, citation/artifact backfill 비의존(#4075/#1084). seq+message 원자 INSERT idempotent | 데이터모델+기술 | §6 |
| M1-partial | **고아 run 정리** — 부팅 시 워커 死 run→`error` 전이로 active-run index 해제(thread 잠금 방지)+승인 타임아웃 | 데이터모델 | §6·§8 |
| F-1 | **cross-thread fork=state-values 시드**(artifact 제외)+thread_id 치환 — checkpoint 바이너리 복사 아님 | 데이터모델+기술 | §6 |
| D-1/C-a | **dual-body 일원화** — 정본=transcript content_md, fork/재생성/재표시는 항상 transcript(checkpoint 초안 아님). 선택: post_model_hook 수렴 | 아키+기술 | §5.1·§6 |
| C-b | **cite 위조 처리=error→재생성 기본**(토큰제거 아님, 법률 안전) | 아키 | §5.1 |
| A | **첫-동결 권위+재적재 불변식** — (uri,eff_date) 동일→article_text 불변(파싱변경=별 id) | 아키 | §4 |
| M3 | **GET /citations 조상 union**+`message_citations(citation_id)` 역참조 인덱스 | 데이터모델 | §3.2·§7 |


### v0.2 개정 (3관점 교차검증 반영)
| 코드 | 해소 | 검증자 |
|------|------|--------|
| C1+m2 | **citation 정규화 분리** — 전역 `citations`(id PK=UUIDv5) + `message_citations` 조인. message 1:N 보존, thread간 PK충돌 해소 | 데이터모델 |
| C2 | **fork=참조 모델 확정** — 새 thread, 메시지 복사 안 함. `GET messages`가 ancestor prefix UNION | 데이터모델 |
| M1 | **seq 카운터 명시** — `threads.last_seq` + 원자 `UPDATE…RETURNING`, active-run partial unique index로 동시run DB강제 | 데이터모델 |
| M2 | **reconcile 절차** — 부팅 시 `runs.checkpoint_id`로 비종료 run backfill | 데이터모델 |
| A | **article_text 동결** — citation에 조문 전문 freeze(법적 무결성), 전역이라 1회 저장 | 아키텍처 |
| B | **§6.2 권위 단일화** — RunService 권위=본 §5, db-layer §6.2는 참조위임(db-layer Design 동반 개정) | 아키텍처 |
| C | **답변계층 책임 확정** — RunService가 **최종 본문** 책임(cite검증·면책·버전). db-layer §6.3 갱신 | 아키텍처 |
| T1 | PostgresSaver **동일 schema 공존**(Python 별 schema 미지원) | 기술 |
| T2 | interrupt=`interrupt_before`/도구내 `interrupt()`(노드 추가 불필요), 도구 Command 미반환·artifact 방출즉시추출 | 기술 |

---

## 1. Overview
### 1.1 Goals
대화가 재시작·재접속에 100% 복원, FE 스레드·이력·근거·분기·요약 조회. **법적 기록 무결성**: citation = 인용 시점 조문 전문 불변 보존.
### 1.2 Principles
transcript=정본(단일 writer RunService·멱등) · citation=방출 순간 불변 스냅샷(전문 포함) · agent는 transcript 비의존 · Zero-Trust 답변계층 서버측 강제.

---

## 2. Architecture
```
frontend ─REST/SSE─▶ backend
   api/        FE §4.1 엔드포인트
   services/   RunService ──┬─▶ FE SSE  └─▶ ConversationRepository(PostgreSQL)
   agent(ReAct) ─ RunService가 오케스트레이션
   checkpointer = PostgresSaver(같은 DB·같은 schema, checkpoint* 4테이블) ← DI 외부주입
```
- **RunService = 단일 권위 소스**(SSE 변환 + 영속 + seq + 답변계층). db-layer §6.2/§6.3은 이 §5를 참조.
- 3계층: ①PostgresSaver ②transcript ③(후속)Qdrant.

---

## 3. Data Model (PostgreSQL)

### 3.1 enum
```sql
CREATE TYPE message_role AS ENUM ('user','agent','tool');
CREATE TYPE run_status   AS ENUM ('running','awaiting_approval','interrupted','completed','rejected','error');
```

### 3.2 transcript (public schema — checkpoint* 4테이블과 이름 비충돌)
```sql
CREATE TABLE threads (
  id UUID PRIMARY KEY, title TEXT, owner_id UUID,
  forked_from_thread_id UUID REFERENCES threads(id),   -- C2 참조 분기
  fork_point_message_id UUID,                           -- 분기 기준(부모 thread 메시지)
  last_seq BIGINT NOT NULL DEFAULT 0,                    -- M1 원자 seq 카운터
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE runs (
  id UUID PRIMARY KEY, thread_id UUID NOT NULL REFERENCES threads(id),
  status run_status NOT NULL DEFAULT 'running',
  checkpoint_id TEXT, checkpoint_ns TEXT,               -- LangGraph config.configurable 노출값
  started_at TIMESTAMPTZ NOT NULL DEFAULT now(), ended_at TIMESTAMPTZ
);
-- M1: thread당 활성 run 1개 DB강제(동시 run 금지) → seq 단일 writer 보증
CREATE UNIQUE INDEX one_active_run_per_thread ON runs (thread_id)
  WHERE status IN ('running','awaiting_approval','interrupted');

CREATE TABLE messages (
  id UUID PRIMARY KEY, thread_id UUID NOT NULL REFERENCES threads(id),
  run_id UUID REFERENCES runs(id),
  seq BIGINT NOT NULL,                                   -- 순서의 정본(thread 내 단조)
  parent_id UUID REFERENCES messages(id),               -- 턴 내 셀 구조(tool↔agent 그룹핑). 분기 아님(분기=thread)
  role message_role NOT NULL, content_md TEXT,           -- markdown([[cite:id]] 포함)
  tool_name TEXT, tool_args JSONB, tool_result JSONB, approval_state TEXT,  -- 도구셀·승인
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  CHECK (parent_id IS NULL OR parent_id <> id),
  UNIQUE (thread_id, seq)                                -- 단조·유일
);
CREATE INDEX ON messages (thread_id, seq);
CREATE INDEX ON threads (owner_id, updated_at);

-- C1/A: 전역 불변 스냅샷(조문-시행본 1행). id 결정적이라 thread간 공유, 전문 동결.
CREATE TABLE citations (
  id UUID PRIMARY KEY,                                   -- = db-layer UUIDv5(article uri)
  kind TEXT, title TEXT, ref TEXT, snippet TEXT, url TEXT,
  article_text TEXT,                                     -- A: 조문 전문 동결(법적 무결성)
  law_uri TEXT, resource_id TEXT, eff_date DATE,         -- db-layer 포인터
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
-- C1: message↔citation 1:N 연결(어느 답변이 무엇을 인용). "이번 답변"=조인, "전체"=thread 메시지 distinct
CREATE TABLE message_citations (
  message_id UUID NOT NULL REFERENCES messages(id),
  citation_id UUID NOT NULL REFERENCES citations(id),
  PRIMARY KEY (message_id, citation_id)
);
CREATE INDEX ON message_citations (citation_id);                -- M3: citation→역참조(GET /citations·"이 근거를 쓴 답변")

CREATE TABLE summaries (
  id UUID PRIMARY KEY, thread_id UUID NOT NULL REFERENCES threads(id),
  covers_from_seq BIGINT, covers_to_seq BIGINT, content_md TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE TABLE settings (scope TEXT PRIMARY KEY, model TEXT, server_url TEXT, theme TEXT,
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now());
```

### 3.3 seq 채번 (M1)
```sql
UPDATE threads SET last_seq = last_seq + 1 WHERE id = $thread RETURNING last_seq;
```
- RunService가 이벤트/메시지마다 원자 채번. `one_active_run_per_thread` 인덱스가 **thread당 동시 run 1개를 DB로 강제** → 단일 writer 보증(advisory lock 불요).
- **★ fork seq 연속성(C2-new, v0.3)**: fork된 자식 thread는 생성 시 `last_seq`를 **부모 fork_point 메시지의 seq로 시드**(자기 메시지는 그보다 큰 seq부터). → 조상 prefix(≤fork_point seq) + 자식 메시지(>fork_point seq)가 **전역 단조**라 `ORDER BY seq` 단일 정렬로 합쳐도 시간순 정확. (시드 안 하면 자식 seq가 1부터라 조상과 뒤섞임.)
  ```sql
  -- /fork 시: 새 thread.last_seq = 부모 fork_point_message.seq
  INSERT INTO threads(..., last_seq) VALUES (..., (SELECT seq FROM messages WHERE id = $fork_point));
  ```

### 3.4 fork = 참조 모델 (C2)
- `/fork` → 새 thread(`forked_from_thread_id`=부모, `fork_point_message_id`=분기점, `last_seq`=fork_point seq로 시드 §3.3). **메시지 복사 안 함**(seq 재채번·id 재매핑 회피).
- `GET /threads/{new}/messages` = (조상 thread별 fork_point seq 이하 prefix) ∪ (새 thread 자체 메시지). `forked_from_thread_id` 체인을 재귀 UNION 후 **`ORDER BY seq`**(§3.3 연속성으로 단일 정렬 정확):
  ```sql
  WITH RECURSIVE chain AS (
    SELECT id, forked_from_thread_id, fork_point_message_id,
           (SELECT seq FROM messages WHERE id = fork_point_message_id) AS cut FROM threads WHERE id = $new
    UNION ALL SELECT t.id, t.forked_from_thread_id, t.fork_point_message_id,
           (SELECT seq FROM messages WHERE id = t.fork_point_message_id)
      FROM threads t JOIN chain c ON t.id = c.forked_from_thread_id)
  -- 각 조상 thread는 seq ≤ cut만, 최종 thread는 cut=NULL→전체. 합집합을 ORDER BY seq.
  ```
- citation 가시성도 같은 prefix(조상 메시지 message_citations) 포함. checkpoint는 부모 fork-point **state values를 새 thread config로 시드**(§6 F-1, checkpoint 복사 아님).

---

## 4. citation 동결 (A·C1)
- **RunService가 `citation.added` 방출 순간** LawRef(전문 `article_text` 포함)를 `citations` insert `ON CONFLICT (id) DO NOTHING`(전역 1회 freeze, 불변). 동시에 `message_citations(message_id, citation_id)` insert(이 답변의 인용).
- 법령 개정돼도 스냅샷 불변. eff_date 다르면 uri 다름→id 다름→별 행(시행본별 보존).
- **법적 무결성**: 과거 답변의 근거 조문 **전문이 citations에 영속**(snippet 200자가 아닌 전체). v0.1의 "미동결"은 정정.
- **★ 첫-동결 권위 + 재적재 불변식(A, v0.3)**: `ON CONFLICT DO NOTHING`이라 **같은 id(=같은 uri=같은 조문·시행본)는 첫 인용 시점 본문이 영속 고정**, 후속 동일 id 인용/db-layer 재적재가 article_text를 덮어쓰지 못함. **의도**: "사용자가 그때 본 근거"를 보존. **전제 불변식**: db-layer는 `(uri, eff_date)` 동일하면 article_text **불변**을 보장해야 함(파싱 변경/정정은 **다른 본문**이므로 별 시행본·별 id로 처리, 같은 id 재의미부여 금지). 첫 동결이 파싱버그본을 고정할 위험은 db-layer 적재 검증(정합성 게이트)이 1차 방어, citations는 "표시 당시 진실" 계층으로 분리.

---

## 5. RunService — 단일 권위 소스 (B·C·db-layer 합류)
agent `stream(stream_mode="updates")` 청크를 RunService가 **단일 지점**에서 (a)SSE 변환 (b)transcript 영속 (c)seq 채번 (d)답변계층 최종본문.

| LangGraph updates | → SSE event(+seq) | 영속 |
|-------------------|-------------------|------|
| 실행 시작 | `run.started{run_id,thread_id,seq}` | runs insert(running) |
| agent 노드 AIMessage.tool_calls | `tool.call{id,name,args,seq}` | messages(role=tool) |
| tools 노드 ToolMessage | `tool.result{id,content,seq}` | message tool_result |
| 법령도구 ToolMessage.artifact(AnswerContext.articles) | `citation.added{id,kind,title,ref,snippet,url,seq}` | citations+message_citations(§4) |
| `interrupt()` (승인 대기) | `approval.requested{id,action,detail,seq}` | runs.status=awaiting_approval·message.approval_state |
| 최종 agent AIMessage | `message.completed{text,content_type:markdown,citations:[id],seq}` | messages(role=agent, content_md=최종본문) |
| 예외/종료 | `error`/`run.done{seq}` | runs.status·ended_at |

- **동시성**: 1차 **thread당 동시 run 금지**(§3.2 active-run index가 DB강제). 추가 메시지는 **409 거부**(큐잉은 후속). FE §5.4 "live 턴 항상 하나"와 정합.
- **커밋 후 방출**: transcript 커밋 후 SSE → 관찰순서=seq=DB순서, 재연결 seq dedup.
- **message.delta 미지원**(updates 모드) → completed로 시작(FE §4.2 각주 합의). 토큰스트림=messages 모드 후속.
- **도구 계약(T2)**: 법령도구는 `Command` 미반환(updates 누락 회피) · `response_format="content_and_artifact"`로 (텍스트, AnswerContext) 반환 · **artifact는 모델입력 제외 + 방출즉시 추출**(checkpoint 재수화 비의존, JSON 직렬화).

### 5.1 답변계층 = RunService가 최종 본문 책임 (C, Zero-Trust)
db-layer §6.3 역할경계를 **갱신**: agent ReAct = **초안 생성**, RunService = **최종 본문 산출**(서버측 강제):
- **cite 위조검증(C-b, v0.3)**: 본문 `[[cite:id]]` ⊆ 이 답변의 message_citations id. **위반 시 기본=`message.completed`→`error`(재생성 유도)** — 법률 답변에서 "토큰만 제거"는 근거 없는 단언을 남겨 더 위험하므로 채택 안 함. (예외: 운영 플래그로 "제거+근거불충분 경고" 모드 선택 가능하나 기본 아님.)
- **면책 고지**: 최종 본문에 "법률자문 아님·전문가 확인" 부착(프롬프트 비의존). `content_md`=최종본문(초안 아님) — **fork/재생성 시 정본은 항상 transcript `content_md`**(checkpoint 초안 아님, §6 D-1).
- **버전 고지**: 답변 머리에 기준 시행일자.

---

## 6. checkpoint(①) + 정합 (T1·M2)
- **PostgresSaver**: 같은 DB·**같은 schema(public)**. Python은 커스텀 schema 미지원이나 LangGraph 테이블(`checkpoints`·`checkpoint_blobs`·`checkpoint_writes`·`checkpoint_migrations`)이 chat 테이블명과 **충돌 없음** → 공존. `.setup()` 배포 1회, `psycopg_pool.ConnectionPool` 장수명 주입(`from_conn_string` 금지).
- **DI 외부주입**: `core/container.py`가 checkpointer 생성·주입(현 agent 내부 `build_checkpointer` 고정→외부주입, db-layer 슬라이스8 함께).
- **★ fork = cross-thread state 시드(F-1, v0.3)**: checkpoint는 **thread 스코프**라 `update_state`만으로 다른 thread_id로 자동 이전되지 않음(native 단일콜 없음). RunService가 명시 구현:
  1. fork-point의 `graph.get_state(부모 thread config).values`(메시지 리스트·요약 등 **artifact 제외**)를 읽어,
  2. **새 thread_id config로 첫 `update_state`/`invoke` 시 주입**(state-values 시드). checkpoint 바이너리 복사 아님(아래 이유).
  - **artifact 제외 이유**: fork-point 메시지에 `ToolMessage.artifact(AnswerContext)`가 있으면 JsonPlusSerializer 직렬화/재수화가 깨짐(#1084/#4075) → checkpoint 복사 경로는 함정. citation은 어차피 transcript `citations`가 정본이므로 state엔 메시지 본문만.
  - cross-thread 시 checkpoint 내부 `thread_id` 참조도 새 값으로 치환(누락 시 resume이 부모 thread를 가리킴).
- **★ dual-body 일원화(D-1, v0.3)**: checkpoint AIMessage=**agent 초안**(그래프 자동영속), transcript `content_md`=**최종본문**(면책·cite검증 후, 그래프 밖). 둘이 갈리므로 **정본=transcript `content_md`로 못박음**: fork/재생성·이력복원·재표시는 **항상 transcript content_md를 시드/표시**(checkpoint 초안 아님). cite 위조 토큰 정합도 transcript 기준. (선택 개선: 면책·검증을 `post_model_hook`으로 그래프 안에서 수행하면 checkpoint=transcript 수렴, dual-body 제거 — 단 면책 보일러플레이트가 LLM 컨텍스트에 남는 trade-off. v1은 transcript-정본으로 충분.)
- **승인(T2)**: `create_react_agent(interrupt_before=["tools"])` 또는 승인대상 도구 내 `interrupt()` — **별 노드 불필요**. `Command(resume=)`로 재개.
- **★ M2 reconcile = transcript 권위(v0.3, M2-new/M2-a)**: 비대칭 크래시(checkpoint 커밋·transcript 누락) 복구. **권위 소스=transcript, checkpoint는 resume 그래프상태 전용**(citation/artifact 복원에 **비의존** — #4075 ToolMessage 재수화 실패·#1084 artifact 직렬화 실패로 checkpoint backfill 불가):
  - 방출 전 크래시 citation은 사용자에게 안 나갔으므로 backfill 대상 아님(드롭 정합). 방출 후·transcript 미커밋은 **seq+message 원자 INSERT로 idempotent 재기록**(checkpoint state에서 citation 읽지 않음).
  - **부팅 시 고아 run 정리(M1-partial)**: 비종료 `runs`(running/awaiting_approval/interrupted) 중 살아있는 워커가 없으면 **`status=error`로 전이**(active-run partial index 해제) → thread 영구 잠금 방지. 재개 가능한 건만 resume.

---

## 7. API (FE §4.1)
threads(POST/GET)·messages(GET/POST→run_id)·stream(SSE)·fork(→thread_id)·summarize·citations·settings(GET/PUT)·models(GET, backend)·runs/{id}/interrupt·approve.
- **GET /threads/{id}/citations**(M3, v0.3): fork thread는 조상 prefix까지 보이도록 §3.4 재귀 chain의 message 집합 ∩ message_citations로 distinct citation 반환(자기 thread만 보면 부모 인용 누락). `message_citations(citation_id)` 인덱스로 역참조.

---

## 8. Error Handling
| 상황 | 처리 |
|------|------|
| 동시 run | **409**(active-run index 위반) |
| transcript write 실패(SSE 후) | seq+message 원자 INSERT idempotent 재기록(§6, checkpoint 비의존) |
| 비대칭 크래시 | 부팅 reconcile = transcript 권위 재기록(§6) |
| 고아 run(워커 死, 비종료 status) | 부팅 시 `status=error` 전이→active-run index 해제(thread 잠금 방지) |
| 승인 무한 대기(awaiting_approval) | 타임아웃 TTL→`error`/`rejected` 전이(thread 재개 가능화) |
| cite 위조 | **기본 error→재생성**(법률 안전, 토큰제거 아님 §5.1) |
| PostgresSaver setup 미실행 | 배포 step·헬스체크 강제 |

---

## 9. Clean Architecture
api→services(RunService·ConversationRepository)→repositories. agent는 ConversationRepository 비의존, checkpointer만 DI. legal_core는 LawRef 값만 소비.

---

## 10. Test Plan
seq 채번 단조·유일(동시), active-run 409, **citation 정규화**(message_citations 1:N·"이번답변/전체"·전역 dedup), article_text 동결·개정 후 불변, fork ancestor-union 가시성, reconcile backfill, updates→SSE 8종, `[[cite:id]]`↔message_citations 1:1, 면책 강제. Integration: PG testcontainers 재시작 복원·PostgresSaver 공존.

---

## 11. Implementation Order (db-layer 슬라이스8 합류)
1. [ ] PG 스키마 + PostgresSaver `.setup()`(공존) + DI 컨테이너 + 법령도구 content_and_artifact화
2. [ ] ConversationRepository(seq 원자채번·citations/message_citations·fork 재귀쿼리)
3. [ ] RunService: updates→SSE 8종 + 영속 + 커밋후방출 + **최종본문(§5.1)**
4. [ ] 승인 interrupt(`interrupt_before`/`interrupt()`) + reconcile
5. [ ] fork/summarize + 이력 복원, FE §4.1
6. [ ] (후속) MemoryProvider(③)

---

## Version History
| Version | Date | Changes | Author |
|---------|------|---------|--------|
| 0.1 | 2026-06-25 | Initial — D-1~8 DDL·RunService | TreeAnderson |
| 0.2 | 2026-06-25 | 3관점 교차검증: citation 정규화(C1)+전문동결(A)·fork 참조모델(C2)·seq 카운터/active-run(M1)·reconcile(M2)·RunService 권위/답변계층(B·C)·PostgresSaver 공존(T1)·interrupt 구체화(T2) | TreeAnderson |
| 0.3 | 2026-06-25 | **2차 3관점 교차검증**(v0.2 파생 모순 수정, 전부 fork↔checkpoint↔seq↔dual-body 수렴): fork seq 연속성 시드(C2-new)·reconcile transcript권위+고아run정리(M2-new/M2-a/M1-partial)·cross-thread state-values 시드+artifact제외(F-1)·dual-body transcript정본 일원화(D-1)·cite위조 error기본(C-b)·첫동결 권위+재적재 불변식(A)·GET citations 조상union+역참조index(M3)·승인 타임아웃 | TreeAnderson |
