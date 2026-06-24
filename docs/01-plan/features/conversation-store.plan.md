# conversation-store Planning Document

> **Summary**: 사용자 채팅 영속 계층 — 스레드·메시지·분기·근거·요약을 PostgreSQL에 보관하고, LangGraph 실행상태는 PostgresSaver로 영속한다. db-layer(법령 지식)와 별개이며 RunService에서 만난다.
>
> **Project**: 2026_06_20_Agent · **Version**: 0.2 · **Author**: TreeAnderson · **Date**: 2026-06-24 · **Status**: Draft (2관점 교차검증 반영)

---

## 0. v0.2 교차검증 반영 (Design 진입 게이트)

2관점 독립검증(데이터모델·계약/통합) 결과: **방향 건전·blocker 없음, Design 진입 가능.** 단 아래를 Design에서 DDL·시퀀스로 못박는다. ★=db-layer와 양방향 합의 필요.

| 코드 | 결정/게이트 | 비고 |
|------|------------|------|
| D-1 | **분기 = thread 레벨**: `/fork`가 새 thread 반환 → `threads.forked_from_thread_id` + `fork_point_message_id`. transcript=정본, `runs.checkpoint_id/ns`로 LangGraph fork 연결 | C1 |
| D-2 ★ | **seq = RunService 단일 writer**, 스코프 `(thread_id, seq)`, **1차는 thread당 동시 run 금지**(단순화). **DB 커밋 후 SSE 방출**(관찰순서=seq 보장). db-layer §6.2에 seq 추가 동기화 | C2 |
| D-3 | **citation 동결 = 방출 순간**(thread+message 스코프) LawRef freeze. 스냅샷 필드 + 포인터(uri/resource_id/eff_date) 분리. id 규칙 = db-layer `UUIDv5` 공유 | M3 |
| D-4 ★ | **승인**: `runs.status` enum에 `awaiting_approval/interrupted/rejected` 추가, 도구셀에 승인상태. 동작(interrupt 노드)은 db-layer 슬라이스8 | 구조 Major |
| D-5 | **정합**: transcript=정본, checkpoint=실행용(재구성 가능). 두 write 사이 크래시 복구 = seq 기반 idempotent 재기록 | M1 |
| D-6 | **PostgresSaver 운영**: `.setup()` 배포 1회, connection pool(요청별 `from_conn_string` 금지), 별 schema 분리. checkpointer는 **container DI 외부주입**(agent 내부 고정 X) | M4, 구조 Minor |
| D-7 | `GET /models`는 backend api 소유(설정엔 선택값만). transcript 비대상 | 구조 Minor |
| D-8 | messages 제약: `CHECK(parent_id<>id)`·사이클 방지·role enum·`UNIQUE(thread_id, seq)`. summary `from_seq/to_seq` 범위. 도구셀↔citation 연결 | M2, m1 |

---

## 1. Overview

### 1.1 Purpose
대화가 재시작·재접속에도 보존되고(현재 인메모리 `MemorySaver`라 휘발), FE가 스레드 목록·이력·근거 라이브러리·분기·요약을 복원·조회할 수 있게 한다.

### 1.2 Background — 3계층 (섞지 않음)
| 계층 | 내용 | 본 피처 |
|------|------|---------|
| ① 실행상태(checkpoint) | LangGraph run 상태(resume/interrupt/승인) | **PostgresSaver로 영속** |
| ② 대화기록(transcript) | 스레드·메시지·도구셀·근거·분기·요약 | **신규 PostgreSQL 스키마** |
| ③ 장기기억(semantic) | 과거대화 의미검색(FE §5.3) | **인터페이스만, 구현 후속** |

> ②는 ①에서 복원 불가(분기트리·근거라이브러리·도구셀·요약) → 별도 transcript 저장 필수.

### 1.3 Related
- FE Plan `frontend/docs/pdca-plan-frontend.md` §4.1(엔드포인트)·§4.2(이벤트)·§5(멀티턴)·D6/D7/D8 — **계약 단일소스**
- db-layer Design §6(RunService·LawRef) — citation 스냅샷·영속 책임이 RunService에 정렬
- 현재 `agent/src/memory/short_term.py`(MemorySaver) — 교체 대상

---

## 2. Scope

### 2.1 In Scope
- [ ] PostgreSQL 스키마: threads·messages(분기 DAG)·runs·citations·summaries·settings
- [ ] LangGraph **PostgresSaver** checkpointer(① 영속, MemorySaver 교체)
- [ ] **분기(fork)** — 원본 보존 + 가지치기 (FE D7)
- [ ] **근거 라이브러리** — 누적·dedup·**불변 스냅샷** (FE D6)
- [ ] **요약** — windowing 마커 (FE D8)
- [ ] `ConversationRepository`(backend 전용) + RunService 영속 통합(이벤트 흐름 중 기록 + `seq` 부여)
- [ ] FE §4.1 엔드포인트 지원: threads·messages·fork·summarize·citations·settings
- [ ] 이력 복원(재접속 시 스레드/메시지/근거 복원)
- [ ] `MemoryProvider` 인터페이스 정의(③, 구현 0)

### 2.2 Out of Scope
- 장기 의미기억 구현(Qdrant) — 후속
- 사용자 인증/계정(별 피처) — 단 settings/소유 스코프 훅은 남김
- FE UI 구현(별 Plan)

### 2.3 의존
- **db-layer RunService**(슬라이스8)와 정렬 — SSE 변환과 transcript 영속이 같은 RunService.
- **★ db-layer §6.2 양방향 동기화 필요**: `seq` 발급(D-2)·승인 이벤트/interrupt 노드(D-4)는 conversation-store 단독으로 못 정함 → db-layer Design §6.2 RunService 표에 seq·approval 추가가 선결.
- **LawRef**(db-layer) → citation 스냅샷 소스(D-3).
- FE §4 계약이 스키마·엔드포인트 제약.

---

## 3. Requirements

### 3.1 Functional
| ID | Requirement | Priority |
|----|-------------|----------|
| FR-01 | threads 생성/목록/조회, messages 목록(스레드별) | High |
| FR-02 | messages 영속(분기 DAG: parent_id·seq·role[user/agent/tool]·markdown·도구셀 jsonb) | High |
| FR-03 | 분기(fork): 원본 보존 + 새 가지 (POST /messages/{id}/fork) | High |
| FR-04 | citations 누적 라이브러리: dedup + **불변 스냅샷**(개정에도 보존) + db-layer 포인터 | High |
| FR-05 | runs 상태 추적(started/ended/status) | High |
| FR-06 | checkpointer = PostgresSaver(resume/interrupt/승인 영속) | High |
| FR-07 | RunService가 이벤트 흐름 중 transcript 영속 + 단조 `seq` 부여(SSE 재연결 중복방지) | High |
| FR-08 | summaries 영속 + windowing(긴 대화 요약, D8) | Medium |
| FR-09 | settings(model/server/theme) 영속 | Medium |
| FR-10 | 이력 복원(재접속 시 스레드·메시지·근거 재구성) | Medium |
| FR-11 | `MemoryProvider` 인터페이스 정의(③ 장기기억, 구현 후속) | Low |

### 3.2 Non-Functional
| Category | Criteria | Measurement |
|----------|----------|-------------|
| Durability | 재시작/재접속 후 스레드·메시지·근거 100% 복원 | 재기동 테스트 |
| **Citation 불변성** | 법령 개정 후에도 과거 답변 근거 스냅샷 불변 | 개정 시뮬레이션 |
| 분기 무결성 | fork 후 원본 트리 보존, DAG 사이클 0 | 단위테스트 |
| seq 단조성 | 동시성 하에서도 thread 내 seq 단조·유일 | 동시성 테스트 |
| 영속 read | 스레드 메시지 로드 p95 < 300ms | 측정 |
| Privacy | 대화 self-host(외부 미전송) | 구성 검토 |

---

## 4. Success Criteria
- [ ] FR-01~07 구현 + 재기동 후 대화 완전 복원
- [ ] fork/근거 dedup/요약 동작, citation 스냅샷 불변 검증
- [ ] PostgresSaver로 interrupt/resume 영속 동작
- [ ] RunService가 transcript 영속 + seq 일관

---

## 5. Risks and Mitigation
| Risk | Impact | Likelihood | Mitigation |
|------|--------|------------|------------|
| ①checkpoint ↔ ②transcript 이중기록 정합 | High | Medium | RunService 단일 기록점, transcript가 정본·checkpoint는 실행용 |
| 분기 DAG 복잡도 | Medium | Medium | append-only + parent_id, head 포인터. 사이클 검증 |
| seq 동시성 충돌 | Medium | Medium | thread별 시퀀스(DB sequence/serializable), run_id+seq 유일 |
| citation dedup 키 모호 | Medium | Low | (kind, law_uri, eff_date) 키 + thread 스코프 |
| 개정으로 스냅샷↔현행 괴리 표시 | Low | Medium | 스냅샷 보존 + "현재 보기" 포인터 분리(FE 라벨) |

---

## 6. Architecture Considerations

### 6.1 Level: Enterprise (production, self-host)

### 6.2 Key Decisions (확정)
| Decision | Selected | Rationale |
|----------|----------|-----------|
| transcript 저장소 | **PostgreSQL** | 스레드/메시지/분기/근거 관계형 적합, JSONB 도구셀 |
| checkpointer | **LangGraph PostgresSaver** | 같은 Postgres 통합, 실행상태 영속 |
| 장기기억 | **인터페이스만(MemoryProvider)** | 후속 Qdrant 구현 |
| citation | **불변 스냅샷 + db-layer 포인터** | 법적 기록 무결성(개정에도 보존) |
| 영속 책임 | **RunService**(이벤트 흐름 중) | SSE 변환과 단일 지점, agent 비의존 |
| 소유 위치 | **backend 전용 ConversationRepository** | agent는 transcript 의존 안 함 |

### 6.3 데이터 흐름
```
agent run → updates → RunService ──┬─▶ FE SSE(이벤트)
                                   └─▶ ConversationRepository(PostgreSQL): messages·citations·runs·seq
checkpoint(①) ─ PostgresSaver(같은 PG) ─ resume/interrupt/approve
재접속 → GET /threads,/messages,/citations → transcript 복원
```

---

## 7. Convention Prerequisites
- 스키마(§0 D-1·D-8 반영): threads(+`forked_from_thread_id`·`fork_point_message_id`)·messages(parent_id↺·seq·role enum·도구셀 jsonb·`CHECK(parent_id<>id)`·`UNIQUE(thread_id,seq)`)·runs(status enum+`checkpoint_id/ns`)·citations(스냅샷+포인터)·summaries(`from_seq/to_seq`)·settings + PostgresSaver 테이블(별 schema)
- citation dedup 키 = (kind, law_uri, eff_date) per thread, id = db-layer UUIDv5 공유
- 환경변수: `DATABASE_URL`(PostgreSQL), (후속)`CHAT_MEMORY_QDRANT_*`
- 소유 스코프 훅(`owner_id`) — 인증 피처 대비 컬럼만 예약

---

## 8. Next Steps
1. [ ] Design(스키마 DDL·ConversationRepository·RunService 영속 시퀀스·fork/summary 알고리즘·FE 엔드포인트 매핑)
2. [ ] db-layer RunService와 계약 정렬
3. [ ] 구현(PostgresSaver 교체 → 스키마 → RunService 영속 → 복원)

---

## Version History
| Version | Date | Changes | Author |
|---------|------|---------|--------|
| 0.1 | 2026-06-24 | Initial (3계층·PostgreSQL·citation 불변스냅샷·장기기억 연기·별도피처 결정 반영) | TreeAnderson |
| 0.2 | 2026-06-24 | 2관점 교차검증 반영: D-1~8 Design 진입게이트(분기=thread레벨·seq 단일writer·citation 동결시점·승인상태·정합·PostgresSaver운영). seq·승인은 db-layer §6.2 양방향 동기화 ★ | TreeAnderson |
