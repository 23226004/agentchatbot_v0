# db-layer Design Document

> **Summary**: 법령 GraphRAG 백엔드 구현 설계 — 본문은 law.go.kr API(단일 신뢰소스), 위임관계는 AI-Hub를 API 재해소로 검증한 엣지만. RDF 온톨로지·repositories 패키지·검색/인제스천 파이프라인·RunService·FE 계약 정렬.
>
> **Project**: 2026_06_20_Agent · **Version**: 0.3.1 · **Author**: TreeAnderson · **Date**: 2026-06-24 · **Status**: Draft (3관점 재검증 통과 — 슬라이스1~6 Do-ready)
> **Plan**: [db-layer.plan.md](../../01-plan/features/db-layer.plan.md) (v0.3) · **Corpus/Join**: [db-layer.corpus-analysis.md](./db-layer.corpus-analysis.md) §6

### v0.3 전제 (3관점 독립 교차검증 반영)
- **본문 = law.go.kr API**(자기정합), **AI-Hub = 위임그래프만**(검증된 엣지), **조인 검증 게이트**(모호 0·실패 격리)
- **임베딩 = FlagEmbedding 자체서버**(TEI는 BGE-m3 sparse 불가), 리랭커 = TEI
- **`legal_core` 독립 패키지**(schemas+repositories) — agent·backend `import src` 충돌 해소
- **RunService** = agent updates→FE SSE 8종 이벤트 변환
- 1차: 법령만·조 단위·현행 단일본 (판례·as-of·위임확장검색은 후속 슬라이스)

---

## 0. v0.3.1 재검증 반영 (3관점 독립 재검증)

데이터·기술·구조 재검증 결과를 반영. **슬라이스1~6 착수 blocker 없음 확인.**

| 코드 | 심각도 | 발견 | 반영 |
|------|--------|------|------|
| **R1** | **필수(구현 전)** | canonical URI `제{조문번호}`가 가지조문(제4조의2 등 흔함)에서 충돌→point.id 덮어쓰기 | §3.2 URI에 **가지번호 포함**(+조문키 유일성) |
| B-1 | MEDIUM | 원격 Fuseki에 rdflib `initBindings` 미적용(#2055) | §9.3 **타입드 바인더(`.n3()`) 1순위**로 정정 |
| B-2 | MEDIUM | Qdrant 필터 payload 인덱스 미명시→full-scan | §3.4 `create_payload_index` 명시 |
| A-2 | 정정 | 위임 실효 = 국가법령 534엣지·해소 후 **~444**(13K는 조례 포함 오해). 격리 ~9%(2018↔2026 명칭드리프트) | §7.2 커버리지 정정·네임스페이스 스코프필터·별칭표 백로그 |
| A-3 | Minor | 별표=별도 API(`target=licbyl`)·부칙=`부칙단위` | §3.5 1차는 본문조문만, 별표/부칙 후속 |
| **C-1** | 슬라이스8 게이트 | RunService에 `approval.requested`+`seq`(재연결 중복방지)+agent `interrupt` 노드 누락 | §6.2 게이트 명시 |
| **C-2** | 슬라이스8 게이트 | 도구가 LLM텍스트+RunService용 구조체를 동시전달할 채널 미정·system prompt `[[cite]]` 미구현 | §6.3 `content_and_artifact` 계약 |
| **C-3** | 슬라이스8 게이트 | pyproject×3·editable install·`_flatten_article` 귀속(db-admin→agent 역의존 방지) | §9.1 legal_core로 이전 |

> 검증 강점 확인: 거실 정의 API 실회수 성공(건축법ID 001823/시행 20260227 라이브 일치), **위임 모호도 0이 80개 표본에서도 성립**, FlagEmbedding 하이브리드·GSP PUT 멱등적재 실현.

---

## 1. Overview

### 1.1 Goals
법령 질의 → 근거 조문 인용 답변을 GraphRAG로. DB 구현을 `legal_core` 경계 뒤로 은닉. 출력을 FE citation과 단일소스 정렬. **데이터 무결성(엉뚱한 연결 0) 최우선.**

### 1.2 Principles
SoC/의존역전 · Zero-Trust(SPARQL 안전 바인딩·입력검증) · Bounded(술어 화이트리스트+상한) · 멱등 인제스천 · **조인은 검증 후에만**.

### 1.3 FR 추적표
| FR | 위치 | FR | 위치 |
|----|------|----|------|
| FR-01 본문수집 | §7.1 | FR-07 패키지 | §9.1 |
| FR-02 위임그래프 | §7.2 | FR-08 의존/RunService | §6.2 |
| FR-03 조인검증 | §7.2, §7.3 | FR-09 신규 | §7.4 |
| FR-04 임베딩/벡터 | §3.4,§4 | FR-10 as-of | §3.3,§5 |
| FR-05 하이브리드+리랭크 | §5 | FR-11 FE정렬 | §6.1 |
| FR-06 bounded 확장 | §4,§5 | FR-12 agent교체 | §6.3,§12 |

---

## 2. Architecture

### 2.1 Component / Package
```
frontend ─REST/SSE─▶ backend(FastAPI)
                     ├ api/        라우터·SSE
                     ├ services/   RetrievalService · RunService(updates→SSE)
                     └ (의존) ─────┐
                                   ▼
agent(ReAct) ─(의존)─────▶  legal_core  [독립 설치형 패키지: pip install -e]
                            ├ schemas/      DenseSparse·Chunk·Hit·LawRef·AnswerContext
                            └ repositories/ GraphRepo(Fuseki)·VectorRepo(Qdrant)
                                            ·Embedding(FlagEmbedding)·Reranker(TEI)
                                   ▲
db-admin(one-shot job) ────────────┘  본문수집·위임그래프+검증·신규
   외부: law.go.kr API · FlagEmbedding 서버 · TEI(rerank) · Fuseki · Qdrant
```
> **임베딩/리랭커 배포 이탈(v5)**: v1 구현은 임베딩·리랭커를 **인프로세스**(`LocalFlagEmbedding`/`LocalFlagReranker`, 모델 프로세스 내 로딩)로 한다 — 슬라이스 검증·단일 프로세스용. 위 도식의 **self-host 서버**는 production 형태(워커당 ~2.5GB RSS·6~10s 콜드스타트 회피, DI 공유). production 전 **서버화 또는 `EMBEDDING_BACKEND=remote` 확정** 필요. **reranker는 정확도 필수**(없으면 정의형 질의 악화) — §8 폴백은 품질저하 동반.
- **3 패키지로 분리(구현 정정 D-1)**: `legal_core`(순수 Protocol·스키마·ids·파싱) + **`legal_infra`(Qdrant/Fuseki/임베딩 구현, 클라이언트 의존 격리)** + 앱(backend_app·db_admin·agent). 앱은 둘 다 의존성 설치. README의 "DB는 repositories 인터페이스로만 접근"을 패키지 경계로 실현(순환·배포결합 제거). SoC상 인터페이스/구현 분리가 더 우월.
- agent/backend는 각자 고유 패키지명(`agent_app`/`backend_app`)으로 정리(`import src` 충돌 제거).

### 2.2 Data Flow
**질의**: 질문→embed(dense+sparse)→VectorRepo.search(hybrid k=30,{is_current})→Reranker(k=8)→GraphRepo.expand(predicates=[lo:delegatesTo],depth≤1,limit≤20)→AnswerContext→(agent ReAct가 GPT 인용답변).
**인제스천**: law.go.kr API→법령/조문 RDF(ELI)+조 청크→embed→Qdrant ‖ **law.go.kr lsStmd(법령체계도)**→base 법령의 위임 하위(법령ID 보유 노드)→delegatesTo(child→base). *(v7: 소스 AI-Hub→lsStmd 변경, AI-Hub는 건축법 미커버. 구조기반 추출=이름매칭 아님.)*

---

## 3. Data Model

### 3.1 Canonical IRI (law.go.kr 법령ID 기반)
| prefix | IRI | 용도 |
|--------|-----|------|
| `kr:` | `https://2026agent.kr/law/` | canonical 법령/조문 |
| `law:` | `http://www.aihub.or.kr/kb/law/` | 채택 어휘(클래스·술어 일부) |
| `eli:` | `http://data.europa.eu/eli/ontology#` | 버전 |
| `dct:` | `http://purl.org/dc/terms/` | 메타 |
| `lo:` | `https://2026agent.kr/ontology#` | `delegatesTo` |

- LegalResource: `kr:법령/{법령ID}` (예 `kr:법령/001823`)
- LegalExpression(시행본): `kr:법령/{법령ID}/{시행일자}` (예 `.../20260227`)
- Article: `kr:법령/{법령ID}/{시행일자}/제{조문번호}[의{조문가지번호}]` — **가지번호 필수**(R1: 제4조의2 등 가지조문이 흔하며 조문번호만으로는 비유일). 유일성 보강으로 payload에 API `조문키`(statute 내 전역유일) 병기. 모두 API 도출(자기정합)

### 3.2 클래스/술어
```
kr:법령/001823            a eli:LegalResource ; dct:title "건축법" ; lo:lawId "001823" .
kr:법령/001823/20260227   a eli:LegalExpression ; eli:realizes kr:법령/001823 ;
                          eli:date_publication "2026-02-27"^^xsd:date ;
                          law:statuteName "건축법" ; lo:hasArticle kr:법령/001823/20260227/제2조 .
kr:법령/001823/20260227/제2조  a law:Article ; law:articleName "제2조" ;
                          law:fullText "제2조(정의) ① ... 6. \"거실\"이란 ..." .   # API 본문(항/호 평탄화)
kr:법령/002118  lo:delegatesTo kr:법령/001823 .   # 건축법 시행령 → 건축법 (검증된 엣지만)
```
- 본문 `fullText`는 law.go.kr `lawService.do`의 조문단위를 항/호까지 평탄화(이미 `agent/src/tools/law.py` `_flatten_article` 로직 재사용).
- **citations(조문↔판례)·hasParagraph(깨짐)·AI-Hub Article는 사용 안 함**(v0.2 오류 정정).

### 3.3 버전(ELI) — as-of
- `eli:date_publication`(시행일자)로 시점 표현. 1차=현행 단일본. 슬라이스6에서 다버전+`superseded` 플래그.

### 3.4 Vector Schema (Qdrant)
- 컬렉션 `law_articles`. named vectors: `dense`(BGE-m3 1024, cosine) + `sparse`(BGE-m3 lexical, Qdrant sparse index). Query API `prefetch`+`{"rrf":{}}` 융합.
- point.id = `UUIDv5(NS, article_uri)`.
- payload: `uri·resource_id(=법령ID)·statute·article_no·article_key(조문키)·eff_date·ministry·text·is_current`.
- **payload 인덱스 생성**(B-2): `is_current`·`eff_date`에 `create_payload_index`(미생성 시 필터 full-scan). prefetch limit > k(예: 각 50).

### 3.5 청크 = 조(Article)
- API 조문단위 1조=1청크(평균 ~300자). 이상치(전문 >2000자) 슬라이딩 분할(겹침 64자).
- **별표·부칙 1차 제외**(A-3): 별표=별도 타깃 `target=licbyl`, 부칙=`부칙단위`. 1차는 본문 조문단위만.
- 임베딩 입력 `"{statute} {article_no}\n{fullText}"`.

---

## 4. Interfaces — `legal_core` (핵심 산출물)

```python
# legal_core/schemas
@dataclass(frozen=True)
class DenseSparse: dense: list[float]; sparse: dict[int, float]
@dataclass(frozen=True)
class Chunk: uri:str; resource_id:str; statute:str; article_no:str; eff_date:str; ministry:str; text:str; is_current:bool
@dataclass(frozen=True)
class Hit: uri:str; score:float; payload:dict
@dataclass(frozen=True)
class LawRef: id:str; kind:str; title:str; ref:str; snippet:str; url:str; uri:str; resource_id:str; eff_date:str; score:float
@dataclass(frozen=True)
class AnswerContext: articles:list[LawRef]; relations:list[tuple[str,str,str]]; query:str

EXPAND_PREDICATES_V1 = ["lo:delegatesTo"]            # 1차 (citations 제거)

class EmbeddingProvider(Protocol):                    # FlagEmbedding 구현
    def embed(self, texts: list[str]) -> list[DenseSparse]: ...
class VectorRepository(Protocol):
    def upsert(self, chunks: list[Chunk], vectors: list[DenseSparse]) -> None: ...
    def search(self, q: DenseSparse, k: int, flt: dict|None=None) -> list[Hit]: ...   # 하이브리드 RRF
class Reranker(Protocol):
    def rerank(self, query: str, hits: list[Hit], k: int) -> list[Hit]: ...
class GraphRepository(Protocol):
    def add_nt(self, path: str, graph_uri: str|None=None) -> int: ...
    def expand(self, uris: list[str], predicates: list[str], depth:int=1, limit:int=20) -> list[tuple[str,str,str]]: ...
    def select(self, template: str, bindings: dict) -> list[dict]: ...   # 안전 바인딩 전용(§9.3)
```
> `expand` 입력 `uris`는 **절대 IRI + 허용 prefix(kr:/lo:) 검증** 후에만(§9.3). raw SPARQL 문자열 API는 제공하지 않음.

---

## 5. 검색 파이프라인 (backend/services/retrieval.py)
```python
class RetrievalService:                # GPT 호출하지 않음 — AnswerContext까지만
    def retrieve(self, query, as_of=None, k=8) -> AnswerContext:
        _validate_iso_date(as_of)
        qv = self.emb.embed([query])[0]
        hits = self.vec.search(qv, 30, {"is_current": True} if as_of is None else {"eff_date<=": as_of})
        if as_of: hits = _latest_per_resource(hits)             # 슬라이스6
        top = self.rer.rerank(query, hits, k)
        # v7: delegatesTo는 법령(resource)간 관계 → 조문 payload의 resource_id로 법령 IRI 확장
        rels = self.graph.expand(uniq(resource_iri(h) for h in top), EXPAND_PREDICATES_V1, 1, 20)
        return AnswerContext([_to_lawref(h) for h in top], rels, query)
```
경계: 30→8→expand depth≤1·limit≤20·술어 화이트리스트.

---

## 6. 계약: FE 정렬 · RunService · 역할 경계

### 6.1 LawRef ↔ FE citation (FR-11)
| FE `citation.added` | LawRef | 생성 |
|---------------------|--------|------|
| id | `str(UUIDv5(NS,uri))` (=point.id) | 단일 규칙 |
| kind | "law" | (판례 시 "precedent") |
| title | statute | |
| ref | f"{statute} {article_no}" | |
| snippet | text[:200] | |
| url | law.go.kr 링크(법령ID/조문) | |

### 6.2 RunService: agent updates → FE SSE (FR-08, 신규)
agent는 `stream(stream_mode="updates")`로 노드 청크를 흘린다. RunService가 FE Plan §4.2 이벤트로 변환:
| LangGraph updates | → FE SSE event | payload |
|-------------------|----------------|---------|
| 스트림 시작 | `run.started` | {run_id, thread_id} |
| AIMessage.tool_calls | `tool.call` | {id, name, args} |
| ToolMessage | `tool.result` | {id, content} |
| **법령도구 ToolMessage(AnswerContext.articles)** | `citation.added`(중복제거) | LawRef→{id,kind,title,ref,snippet,url} |
| 최종 AIMessage.content | `message.completed` | {text, content_type:"markdown", citations:[id]} |
| 예외 / 종료 | `error` / `run.done` | |
> **message.delta**: 현재 updates 모드라 **미지원** → `message.completed`로 시작(FE Plan §4.2 각주와 합의). 토큰 스트림은 `stream_mode="messages"` 도입 슬라이스로 분리.
> **슬라이스8 게이트(C-1)**: ① `approval.requested{id,action,detail}` + FE `/approve`·`/interrupt` → **agent 그래프에 LangGraph `interrupt`/`Command(resume=)` 노드 도입**(현 `create_react_agent`엔 없음). ② 모든 이벤트에 단조 `seq` 부여(SSE 재연결 중복방지, FE §5.5/§11).

### 6.3 역할 경계 (이중경로 방지)
- **검색·관계확장 = RetrievalService 단독** (반환 AnswerContext, GPT 미호출).
- **GPT 인용답변 생성 = agent ReAct 단독.** 법령도구가 RetrievalService를 호출→AnswerContext를 도구결과로 반환→agent LLM이 본문에 **`[[cite:{LawRef.id}]]`** 주입(system prompt 규칙)하여 답변.
- **SSE citation.added 방출 = RunService** (도구결과의 LawRef[]에서). 본문 토큰 id ≡ citations id ≡ point.id (1:1).
- **도구 반환 계약(C-2, 슬라이스8 게이트)**: 법령도구는 LangChain `response_format="content_and_artifact"`로 **(LLM용 텍스트: 각 LawRef.id 라벨 포함, AnswerContext: 구조체)를 동시 반환**. LLM은 텍스트의 id로 `[[cite:{id}]]` 주입(system prompt 규칙 추가 필요 — 현 `prompts/system.py` 미구현), RunService는 artifact의 LawRef[]로 citation.added 방출.

---

## 7. Ingestion (db-admin, one-shot job)

### 7.1 본문 수집 `lawgo_ingest.py` (FR-01)
law.go.kr `lawSearch`→법령ID/MST/시행일자, `lawService`→조문단위. → LegalResource/Expression/Article RDF(ELI) `add_nt` + 조 청크 `embed`→`upsert`. 멱등(UUIDv5, named graph per Expression).

### 7.2 위임그래프 `delegation_build.py` (FR-02) — v7: lsStmd 기반
```
base = resolve_current(base_name)                       # FR-03 게이트(base, 정확명+현행 유일)
children = parse_hierarchy(lsStmd(base.mst), base.id)   # 상하위법 트리의 법령ID 노드(법종≠법률)
for cid in children:                                    # 이름 prefix 아님 — 구조 기반(누락 해소)
    emit  kr:법령/{cid} lo:delegatesTo kr:법령/{base.id}   # child→base, named graph/delegation/{base.id}
report = {base_id, edges, children}
```
> **v7 변경**: 소스 AI-Hub→**lsStmd**(AI-Hub 건축법 미커버). child는 lsStmd가 권위적 법령ID 제공 → child resolve 불요(모호 본질적 없음). **잔여(minor)**: child 현행여부 검증·격리 리포트 미구현(lsStmd가 현행 트리라 위험 낮음). 자치법규(조례)는 법령ID 없어 자동 제외=1차 스코프.
- **resolve()**: `search_law` 결과에서 `법령명한글==name && 현행연혁코드=="현행"` 가 정확히 1개일 때만 유일ID. (80표본 모호 0 검증)
- **스코프·커버리지 정정(A-2)**: 상위법령 13,173엣지 중 **국가법령→법령은 534개**(나머지 96%는 자치법규(조례), 네임스페이스 `/자치법규/`로 필터 제외). 해소율 ~91%·격리 ~9%(2018↔현행 명칭드리프트: 문화재청→국가유산청 등) → 양끝 생존 ~83% → **실효 위임엣지 ≈ 444개**(13K 아님). 격리는 데이터 손실이지 오염 아님.
- **격리 완화**: 명칭변경 별칭 매핑표를 백로그로 점진 보강(R5).

### 7.3 검증 게이트 `inspector/` (FR-03)
- delegation 리포트 산출. **ambiguous>0 이면 적재 중단**(수동 매핑 후 재시도). 격리 목록 로그 보존.
- raw SPARQL 점검 쿼리는 여기 전용(§9.3).

### 7.4 신규 `ingest_new.py` (FR-09): KDS 등 어댑터(동일 검증 게이트 적용).

---

## 8. Error Handling
| 상황 | 처리 |
|------|------|
| 리랭커 다운 | 폴백: 벡터검색만(리랭크 생략)+경고 |
| 임베딩 다운 | 503 (dense/sparse 불가→검색 불가) |
| Qdrant 0건 | 빈 AnswerContext → "근거 없음" |
| SPARQL 오류/타임아웃 | 관계확장 생략(검색만) |
| 위임엣지 모호/미해소 | 격리+로그(적재 제외) — 엉뚱한 연결 방지 |
| 잘못된 as_of/IRI | 400 (ISO date·IRI 검증 실패) |

---

## 9. Clean Architecture

### 9.1 패키지/레이어 (FR-07)
| 단위 | 위치 | 의존 |
|------|------|------|
| `legal_core` | 독립 설치형 패키지 | schemas←(없음), repositories←schemas+클라이언트 |
| `backend_app` | backend/ | legal_core, (api→services→legal_core) |
| `agent_app` | agent/ | legal_core (도구가 RetrievalService 경유) |
| `db-admin` | db-admin/ | legal_core + law.go.kr 클라이언트 |
- import 규칙: api→services→repositories(Protocol). schemas는 순수. **services는 raw SPARQL 금지**.
- **마이그레이션 실체(C-3)**: ① `legal_core/pyproject.toml` + agent/backend 각 `pyproject.toml`(고유 패키지명, `legal_core`를 editable 의존). ② agent `src→agent_app` rename + `from src.` 13곳 치환 + 진입점 `python -m agent_app.main`. ③ **`_flatten_article`(현 `agent/src/tools/law.py`)을 `legal_core`로 이전** — db-admin이 agent_app을 역import하는 순환 방지. (이상 슬라이스1 체크리스트)

### 9.2 DI
`backend_app/core/container.py`가 env로 구현 선택(Fuseki/Qdrant/FlagEmbedding/TEI) 주입. agent도 동일 컨테이너에서 RetrievalService 주입. 테스트는 페이크.

### 9.3 SPARQL 안전 (Zero-Trust)
- **타입드 바인더가 1순위**(B-1): `URIRef`/`Literal`만 받아 검증 후 **`.n3()` 이스케이프**로 치환. 원격 Fuseki에는 rdflib `SPARQLStore.initBindings`가 **미적용(신뢰 불가, rdflib #2055)** — 의존하지 않는다. 문자열 보간 금지.
- `expand`(VALUES 다중 IRI)/`select` 입력 IRI는 **절대 IRI + 허용 prefix(kr:/lo:/law:)** 검증 후 `.n3()` 직렬화. 읽기=`SPARQLWrapper`, 쓰기/벌크=Fuseki GSP PUT(`add_nt`, named graph 멱등 교체). integration 테스트에 인젝션 입력 케이스 포함.

---

## 10. Conventions (Python)
타입힌트 전면+`from __future__ import annotations` / PascalCase·snake_case·UPPER_SNAKE / 구현 접미사 `_fuseki·_qdrant·_flag·_tei` / 비밀 env / 도구 예외 문자열 반환.

---

## 11. Test Plan
| Type | Target | Tool |
|------|--------|------|
| Unit | RetrievalService(페이크)·`_latest_per_resource`·LawRef.id·resolve()·_validate(iso/iri) | pytest |
| Integration | Fuseki/Qdrant testcontainers: add_nt→search→expand | pytest+testcontainers |
| **Join 검증** | 위임 resolve 모호=0·격리 집계 | 검증 스크립트 |
| E2E(slice) | 건축법 API본문→"거실 정의"→인용 | 스크립트 |
| 성능/평가 | p95<1.5s · Recall@10≥0.9(현행) | 부하·골든셋 |
| 계약 | RunService updates→SSE 8종 · `[[cite:id]]`↔citations 1:1 | pytest |

---

## 12. Implementation Order (수직 슬라이스)
1. [ ] `legal_core` 패키지 골격(schemas+repositories Protocol) + agent/backend 패키지명 정리
2. [ ] docker-compose: Fuseki·Qdrant·FlagEmbedding·TEI(rerank)
3. [ ] 구현체: vector_qdrant·graph_fuseki·embedding_flag·reranker_tei
4. [ ] db-admin `lawgo_ingest`: **건축법 현행** API 본문→RDF+임베딩
5. [ ] RetrievalService + 단위테스트(페이크)
6. [ ] **E2E(1차)**: "거실 정의" API본문 GraphRAG 인용 (위임확장·as-of 없음)
7. [ ] `delegation_build`+`inspector`: 위임그래프+검증게이트(리포트 모호0) → expand에 delegatesTo 활성화
8. [ ] RunService(updates→SSE) + agent 도구를 RetrievalService로 교체(FR-12)
9. [ ] (슬라이스6) 다버전 시드 + as-of

---

## Version History
| Version | Date | Changes | Author |
|---------|------|---------|--------|
| 0.1 | 2026-06-24 | Initial | TreeAnderson |
| 0.2 | 2026-06-24 | design-validator 반영(C-1~3·M-1~5) | TreeAnderson |
| 0.3 | 2026-06-24 | 3관점 독립검증: 본문=API·위임=검증된 AI-Hub·조인게이트·FlagEmbedding·legal_core 패키지·RunService·SPARQL안전 | TreeAnderson |
| 0.3.1 | 2026-06-24 | 3관점 재검증 반영: R1 가지조문 URI·§9.3 .n3() 1순위·payload인덱스·위임커버리지 정정(~444)·슬라이스8 게이트(approval/seq·도구artifact·pyproject/_flatten) | TreeAnderson |
