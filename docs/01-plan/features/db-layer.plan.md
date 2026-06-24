# db-layer Planning Document

> **Summary**: 한국 법령 GraphRAG 백엔드 — 본문은 law.go.kr 실시간 API(단일 신뢰 소스), 관계(위임)는 AI-Hub 그래프를 **API 재해소로 검증한 엣지만** 사용. Fuseki(RDF)+Qdrant(vector)+FlagEmbedding(BGE-m3)+리랭커로 조문 근거 답변.
>
> **Project**: 2026_06_20_Agent · **Version**: 0.3 · **Author**: TreeAnderson · **Date**: 2026-06-24 · **Status**: Draft

---

## 0. v0.3 개정 요약 (3관점 독립 교차검증 반영)

bkit design-validator(91점)는 문서 내부정합만 봐서 **데이터/기술/구조의 Critical을 못 잡았다.** 데이터 실증·기술 실현성·아키텍처 3관점 독립 검증으로 아래를 정정.

| # | 변경 | 근거 |
|---|------|------|
| P1 | **본문 소스 = law.go.kr 실시간 API** (AI-Hub nt 폐기) | AI-Hub `citations`=조문→판례(상호참조 아님), Article fullText=제목만, hasParagraph 100% 단절, 건축법/거실 부재 |
| P2 | **AI-Hub = 위임 관계그래프만** (`상위법령`→`delegatesTo`, 공짜) | 13,173엣지 라벨 100%. 판례·용어 2차 |
| P3 | **조인 정합성 검증 게이트** (엉뚱한 연결 방지) | 법령명→유일 법령ID 해소 실측 모호 0, 실패 격리 |
| P4 | **임베딩 서버 = FlagEmbedding** (TEI 아님) | TEI는 BGE-m3 sparse 불가. FlagEmbedding lexical_weights=설계 타입 일치 |
| P5 | **repositories+schemas 독립 패키지 분리** | agent·backend `import src` 충돌, 배포 결합 |
| P6 | **RunService(updates→SSE) 명세** | FE 8종 이벤트 변환 책임 공백 |

---

## 1. Overview

### 1.1 Purpose
법령 질의에 **정확한 조문 근거**로 답하는 데이터 계층. 여러 법령을 의미로 검색(Vector)하고 위임 관계를 순회(RDF Graph)하는 GraphRAG.

### 1.2 Background
- 위임 체인(법률→시행령→시행규칙)은 그래프가 본질. 시행일자별 버전이 정확성 핵심.
- production 규모(분리 서버, 축소 없음). 그래프 표현은 **N-triples(RDF)** — 근거: ELI 버전표준 정렬 + 위임 데이터가 이미 RDF(`상위법령`).
- **데이터 신뢰성이 최우선**: 잘못 연결된 근거는 법률 도메인에서 치명적 → 조인은 실증 검증 후에만(§3 FR-03).

### 1.3 Related Documents & Assets
- 루트 `README.md`(5계층 SoC), FE Plan `frontend/docs/pdca-plan-frontend.md`(§4 citation·SSE 계약 단일소스)
- 검증된 도구 `agent/src/tools/law.py` (law.go.kr `search_law`/`get_law_articles` — **본문 소스로 승격**)
- 설계: `docs/02-design/features/db-layer.design.md` (v0.3), 코퍼스·조인검증: `db-layer.corpus-analysis.md` §6
- AI-Hub 코퍼스: `jikji_공부/project/Agent_00/tools/LAW_SEARCH_TOOL/data` (관계그래프용)

---

## 2. Scope

### 2.1 In Scope
- [ ] law.go.kr API로 법령 본문 수집 → 조 단위 청크 → 임베딩 → Qdrant
- [ ] 법령 RDF 그래프 생성(law.go.kr API 기반: 법령·조문·시행본, ELI 버전)
- [ ] AI-Hub `상위법령` → `delegatesTo` 위임그래프, **API 재해소 검증 엣지만**
- [ ] **조인 검증 게이트**(법령명→유일ID, 모호=0, 실패 격리+로그)
- [ ] `repositories`+`schemas` 독립 패키지 + Fuseki/Qdrant/FlagEmbedding/리랭커 구현
- [ ] 검색: 하이브리드(dense+sparse)→리랭크→bounded SPARQL 확장
- [ ] RunService(agent updates→FE SSE 8종 이벤트 변환)
- [ ] docker-compose(Fuseki·Qdrant·FlagEmbedding·리랭커·backend)
- [ ] 수직 슬라이스: 건축법(API 본문) → "거실 정의" GraphRAG 인용

### 2.2 Out of Scope
- 프론트엔드 구현(별 Plan), 판례·용어 그래프(2차), KDS·VWORLD(3차), 인증, K8s 운영 매니페스트

### 2.3 의존
- FE Plan §4 계약(citation/SSE)이 출력 스키마·RunService를 제약.

---

## 3. Requirements

### 3.1 Functional Requirements
| ID | Requirement | Priority |
|----|-------------|----------|
| FR-01 | law.go.kr API로 법령 본문 수집(법령ID+시행일자+조문번호 정체성) → RDF(ELI)+조 청크 | High |
| FR-02 | AI-Hub `상위법령`→`delegatesTo` 위임그래프 생성 | High |
| **FR-03** | **조인 검증 게이트**: 양끝 법령명→유일 법령ID 재해소, 모호=0, 실패 격리+로그. 검증 통과 엣지만 활성화 | **High** |
| FR-04 | 조 청크 임베딩(FlagEmbedding dense+sparse)→Qdrant(조문 URI 조인키) | High |
| FR-05 | 질의→하이브리드 검색→리랭크(BGE-reranker-v2-m3)→상위 조문 | High |
| FR-06 | bounded SPARQL 관계확장(술어 화이트리스트=delegatesTo 등, 깊이·개수 상한) | High |
| FR-07 | `repositories`+`schemas` 독립 패키지(공유), 모든 DB 접근 은닉 | High |
| FR-08 | 의존 `frontend→backend→agent`, RunService가 agent updates→FE SSE 변환 | High |
| FR-09 | 신규 소스(KDS 등) 확장 ingestion 인터페이스 | Medium |
| FR-10 | as-of-date 버전 질의(law.go.kr 시행일자, ELI) | Medium |
| FR-11 | 출력 `LawRef` ⊇ FE citation `{id,kind,title,ref,snippet,url}` | Medium |
| FR-12 | agent 법령 도구가 repositories 추상화로 GraphRAG 검색 | Medium |

### 3.2 Non-Functional Requirements
| Category | Criteria | Measurement |
|----------|----------|-------------|
| **Data integrity** | 위임엣지 조인 모호도 0, 미해소 엣지 격리율 기록 | 검증 리포트 |
| Performance | 검색 p95 < 1.5s (캐시 제외) | 부하 측정 |
| Accuracy | 조문 검색 Recall@10 ≥ 0.9 (현행 골든셋) | 평가 |
| Privacy | 본문 외부 미전송(임베딩 self-host) | 구성 검토 |
| Reproducibility | docker-compose 1-command | 로컬 검증 |
| Decoupling | DB 구현 교체가 repositories 경계 내 | 정적 검토 |

---

## 4. Success Criteria
- [ ] FR-01~08 구현 + 수직 슬라이스 동작
- [ ] **조인 검증 리포트: 위임엣지 모호 0, 격리 건수 명시**
- [ ] "거실 정의"가 API 본문 GraphRAG로 근거 인용 답변
- [ ] `LawRef`↔FE citation 정렬, RunService 이벤트 변환 동작
- [ ] repositories 패키지 단위테스트 통과, 의존방향 위반 0

---

## 5. Risks and Mitigation
| Risk | Impact | Likelihood | Mitigation |
|------|--------|------------|------------|
| **엉뚱한 연결**(법령 오매핑) | **High** | Medium | FR-03 검증 게이트: 정확명+현행 유일ID만, 실패 격리. 본문은 단일 API 소스(교차조인 없음) |
| API rate limit/장애 | Medium | Medium | 수집 캐시·백오프, 본문 스냅샷 보관 |
| 개정 명칭변경으로 위임엣지 미해소 | Medium | Medium | 격리+로그, 별칭 매핑 테이블 점진 보강 |
| 관계확장 무경계 컨텍스트 폭증 | High | Low | 술어 화이트리스트 + depth·limit 상한 |
| GPU 단일 임베딩+리랭커 경합 | Medium | Medium | 길이 버킷팅·배치, 용량 측정 |
| 패키지 분리 리팩터링 범위 | Medium | Medium | repositories/schemas만 우선 분리, 점진 |

---

## 6. Architecture Considerations

### 6.1 Level: **Enterprise** (분리 서버, production)

### 6.2 Key Decisions (확정)
| Decision | Selected | Rationale |
|----------|----------|-----------|
| 본문 소스 | **law.go.kr 실시간 API** | 유일 신뢰 소스(자기정합), AI-Hub 본문 결함 |
| 관계 소스 | **AI-Hub `상위법령`(검증 후)** | 위임그래프 공짜, 단 API 재해소 검증 |
| 그래프 | RDF/N-triples, **Fuseki** | ELI 버전 + SPARQL |
| Vector | **Qdrant** | dense+sparse 하이브리드 RRF |
| 임베딩 | **FlagEmbedding 자체서버(BGE-m3)** | TEI는 sparse 불가 |
| 리랭커 | **BGE-reranker-v2-m3(TEI)** | TEI rerank 지원 |
| LLM | GPT(gpt-5.4-nano) | 구성됨 |
| 패키지 | **repositories/schemas 독립 설치형** | agent·backend 공유, `import src` 충돌 회피 |

### 6.3 의존 방향 & 레이어
```
frontend ─REST/SSE─▶ backend(FastAPI) ─▶ agent(ReAct)
   RunService: agent updates → FE SSE 8종 이벤트 변환
        └────────────┬──────────────┘
                     ▼
          legal_core (독립 패키지): schemas + repositories
          GraphRepo(Fuseki)·VectorRepo(Qdrant)·Embedding(FlagEmbedding)·Reranker(TEI)
                     ▲
          db-admin (one-shot job): law.go.kr 본문수집 · 위임그래프+검증 · 신규
```

---

## 7. Convention Prerequisites

### 7.1 조인키 규칙 (FR-03 핵심)
- 법령 정체성 = **law.go.kr 법령ID**(API). 조문 = 법령ID+시행일자+조문번호.
- AI-Hub→canonical 매핑 = **법령명 정확일치 + 현행 → 유일 법령ID**. AI-Hub 내부ID 사용 금지.
- 미해소(개정/폐지/조례) = 격리. 강제연결 금지.

### 7.2 환경변수
`FUSEKI_URL`·`FUSEKI_DATASET`·`QDRANT_URL`·`QDRANT_COLLECTION`·`EMBEDDING_URL`(FlagEmbedding)·`RERANKER_URL`·`LAW_API_OC`(기존)·`AIHUB_REL_PATH`

---

## 8. Next Steps
1. [ ] Design v0.3 (온톨로지·인터페이스·조인검증·파이프라인·RunService)
2. [ ] docker-compose 인프라 기동
3. [ ] repositories 패키지 + 조인검증 ingestion + 수직 슬라이스

---

## Version History
| Version | Date | Changes | Author |
|---------|------|---------|--------|
| 0.1 | 2026-06-24 | Initial | TreeAnderson |
| 0.2 | 2026-06-24 | 1차 교차검증(데이터 이원화·의존방향·RDF근거·FE정렬) | TreeAnderson |
| 0.3 | 2026-06-24 | 3관점 독립검증(본문=API·관계=검증된 AI-Hub·조인게이트·FlagEmbedding·패키지분리·RunService) | TreeAnderson |