# db-layer Gap 분석 (PDCA Check)

> **대상**: db-layer v1 수직슬라이스(1~6) · **기준**: Design v0.3.1 · **분석일**: 2026-06-24
> **방법**: gap-detector — Design↔구현 추적, 의도적 보류와 누락 구분

## 결과 요약
> ⚠️ **아래 표는 1차(gap-detector) 수치이며 v2·v3에서 정정됨. 정정 최종치는 맨 아래 v3 참조.**

| 지표 | 1차값 | **정정(v3)** |
|------|-----|-----|
| 1차 슬라이스(1~6) 일치율 | ~~95%~~ | **~85%** (하이브리드 dense축소 가중 반영) |
| 순수 누락 | ~~0~~ | 테스트 자산 영속·integration 추가로 **해소(당시엔 scratchpad-only)** |
| 이탈 | LOW 3 | + dense축소·E2E 인용주체(아래) |

## FR 상태
| 구현됨 | 후속보류(설계 명시) |
|--------|---------------------|
| FR-01 본문수집 · FR-06 bounded확장 · FR-07 패키지 · FR-11 FE정렬 · FR-03 조인게이트(부분) · FR-04 벡터(dense) | FR-02 위임그래프(S7) · FR-05 리랭크 · FR-08 RunService(S8) · FR-09 신규소스 · FR-10 as-of(S6) · FR-12 agent교체(S8) |

## 입증된 설계 준수
- 인터페이스: Protocol 4종 시그니처 = Design §4 (runtime_checkable, 구현 만족)
- 데이터모델: canonical IRI 가지번호(R1)·ELI 트리플·Vector payload = Design §3
- 결정: 본문=API · 조인게이트(resolve_current 유일해소) · `.n3()` 안전바인딩 · dedup · 역할경계(GPT 미호출)
- 테스트 17/17 (R1 회귀·인젝션·dedup·citation id 규칙)

## 이탈 (LOW, 모두 충돌 없음)
| # | 항목 | 처리 |
|---|------|------|
| D-1 | 구현체를 `legal_infra` 별 패키지로 분리(설계도식은 legal_core 내 포함) | **개선** — Design §2.1/§9.1 도식을 2패키지로 정정(완료) |
| D-2 | `select`가 default graph만 조회(expand는 UNION) | 백로그 — 적재가 named graph라, select 사용(S6+) 전 GRAPH절/unionDefaultGraph 적용 |
| D-3 | `_law_url`이 법령명 기반(ID 아님) | 백로그 — 동명법령 모호. 법령ID 기반 URL로 |

## 즉흥 결정 (검증 — 합당)
- `MAX_CHARS=450` 분할(ubatch 512토큰 실측 근거) · 글자예산 배치(n_ctx 보호) · `_dedup_by_article`(citation id 단일규칙 강화)

## 판정 (1차 — gap-detector)
~95%, Check 통과. **단 아래 v2 교차검증에서 과대평가로 정정됨.**

---

## v2 — 3관점 독립 교차검증 + Act (정정)

gap-detector 판정을 **런타임·테스트품질·아키텍처 3관점**으로 적대 재검 → "95%·누락0"이 **부풀려짐** 확인. 발견 + 조치:

| 발견 | 심각도 | 조치(Act) |
|------|:---:|------|
| 테스트/E2E/적재가 **scratchpad에만**(레포 부재) → 회귀·재현 불가 | Major | ✅ **레포 영속**: 단위테스트 17→**38**, `db-admin/scripts/ingest_cli.py`·`backend/scripts/e2e_geosil.py` 승격(E2E 재현 PASS) |
| **#3 인젝션**: `.n3()` 우연 Exception 의존, `_validate_iri` 악성IRI 통과·술어 무검증 | Major | ✅ **수정**: 불법문자 정규식 차단→`ValueError` 계약, 술어도 검증. 회귀테스트 추가(라이브 확인) |
| **#4 as-of silent wrong**: 필터 통째 skip→전버전 검색 | Major | ✅ **수정**: `as_of` 지정 시 `NotImplementedError`(조용히 틀림→명확 실패). 테스트 추가 |
| **FR-03 과대분류**: resolve_current는 FR-01 일부, 위임검증(inspector·격리)은 0코드 | Major | ✅ **재분류**: FR-03 → 슬라이스7(후속). 일치율 분자에서 제외 |
| **하이브리드→dense 축소 미표기**(Recall NFR) | Major | ⚠️ **이탈 명시**: FR-04는 dense-only로 축소(sparse 후속). Recall 목표는 sparse·reranker 도입 후 재측정 |
| **D-2 select default-graph**: 슬라이스6 as-of 0건 시한폭탄 | Major(S6) | ⚠️ **슬라이스6 진입게이트** 승격(#4가 as-of를 막아 v1 무해). S6 전 unionDefaultGraph/GRAPH절 필수 |
| `_dedup_by_article` 2등 서브청크 본문손실 | Minor | 백로그 |
| 미검증 핵심로직(parse/split/resolve/filter) | High | ✅ 단위테스트 추가(test_lawgo·test_text·test_filter·test_resolve) |
| point.id/citation seq 정합 | — | 확인: **정합 양호** |

### 정정된 일치율
- 1차 "95%"는 ① FR-03 미착수를 부분구현으로 계수 ② scratchpad-only 자산을 "통과"로 계수해 부풀려짐.
- **정정 후(슬라이스1~6 적정범위)**: FR-01·06·07·11 구현 + FR-04 dense축소(부분) → **체감 ~88%**. Act로 자산 영속·실버그2 수정 완료하여 **Check 통과(조건 해소)**.

### 남은 백로그 / 슬라이스6+ 게이트
- D-2 select named-graph(unionDefaultGraph) · 하이브리드 sparse · reranker · testcontainers integration · _dedup 본문손실 · D-3 URL(법령ID기반)

### 최종 판정 (v2)
**Check 통과 — 단 "정직한 v1": 검증자산 레포 영속·실버그 수정 완료. dense-only 축소와 D-2는 슬라이스6 진입 전 닫을 게이트로 명시.** 코드 품질·안전성·멱등성은 런타임 실행으로 견고 확인.

---

## v3 — Act 재검증 (3관점 재가동) 최종

v2 수정을 같은 3관점으로 재검 → **두 Major(런타임 #3·#4) 실행 증거로 닫힘 확인**, 테스트 공백 보강, 분석서 정직성 최종 정정.

### 재검 결과
- **런타임**: #3 인젝션 `ValueError` 계약·#4 as-of `NotImplementedError` 라이브 확인. 한글 canonical IRI 오탐 없음. 멱등·dedup·검색 회귀 없음. 잔여 Critical/Major **0**.
- **테스트**: 신규 테스트 theater 아님, 최우선 공백(parse·split·resolve·filter) 실로직 타격 확인. **추가 보강**: 라이브 integration(add_nt→expand named-graph·upsert→search→필터)·embedding 배치경계·parse(목 4단계·비정수)·src-layout 재현러너. **총 46 케이스 통과**(`scripts/run_tests.sh`, 인프라 미가동 시 integration skip).
- **아키텍처**: v2가 5개 지적 정직 수용 확인. 잔여 정직성 정정 ↓.

### v3 정정 (아키텍처 검증자 Major 반영)
| 항목 | 정정 |
|------|------|
| **E2E 인용주체** | E2E(`backend/scripts/e2e_geosil.py`)의 GPT호출·`[[cite:id]]` 주입은 **스크립트가 선대행**. **프로덕션 인용생성 주체 = 슬라이스8(agent ReAct + RunService)이며 그 전환은 미검증.** E2E PASS는 "검색→근거→인용 흐름 재현"을 입증할 뿐 역할경계 프로덕션 구현을 입증하지 않음 |
| **Integration** | "백로그" → **라이브 integration 테스트 추가 완료**(실 Qdrant/Fuseki 계약). 컨테이너화(testcontainers) CI 자동화는 후속 |
| **일치율** | 88% → **~85%**: 하이브리드(dense+sparse)는 Recall NFR 핵심인데 dense-only로 절반 축소 → 가중 낮춤. v1은 Recall NFR **미측정** |
| **테스트 수** | "17→38"(부정확) → **46 케이스 통과**(함수 ~38). 재현: `scripts/run_tests.sh` |
| 패키징 | legal_core·legal_infra **src-layout** 전환(flat 섀도잉 해소), 4패키지 editable |

### 최종 판정 (v3)
**v1 슬라이스 Check 통과 (정직 ~85%).** 두 실버그 코드로 봉인, 검증자산 레포 영속+라이브 integration, 분석서 정직 정정 완료. **슬라이스8 진입 전 게이트**(미검증·미구현, 누락 아님): ① 인용생성 프로덕션 주체(agent ReAct+RunService) ② 하이브리드 sparse+reranker→Recall 측정 ③ as-of(D-2 select named-graph) ④ testcontainers CI ⑤ 위임그래프(delegatesTo). 코드 품질·안전성·멱등성은 런타임 실행으로 견고.

---

## v4 — Act: 하이브리드 + reranker 구현 (일치율 상향)

dense-only 축소(FR-04 부분)와 FR-05 미구현이 ~85%를 누르던 핵심 → **인프로세스 FlagEmbedding으로 구현**.

### 구현
- **FR-04 하이브리드 ✅**: `LocalFlagEmbedding`(BGE-m3 dense+sparse `{token:weight}`) + Qdrant 하이브리드(dense·sparse prefetch → RRF 융합). sparse 슬롯 실제 채워짐.
- **FR-05 reranker ✅**: `LocalFlagReranker`(BGE-reranker-v2-m3). **FlagEmbedding reranker가 transformers 5.x 비호환(`prepare_for_model` 제거) → transformers `AutoModelForSequenceClassification` 직접 구현**으로 우회.
- 임베딩/검색 동일 모델 정합(ingest_cli·container 둘 다 FlagEmbedding 기본, `EMBEDDING_BACKEND=remote`로 폴백).

### 구현 중 발견·수정 (검증자 예언 적중)
- **하이브리드가 정의형 질의엔 오히려 악화**: "거실 정의"에 제53조("지하층 거실 금지", 키워드 밀도↑)가 sparse로 1위 →정작 정의(제2조) 밀림. **reranker가 교정**(제2조 1위로).
- **rerank-before-dedup**(아키텍처 검증자 v2 Minor 적중): dedup이 rerank보다 먼저면 긴 조문의 '엉뚱한 윈도우'가 대표로 생존→본문손실. **순서를 rerank→dedup으로** 수정.
- **답변엔 조문 전체 본문 필요**: 윈도우 경계가 정의를 잘라 GPT가 "잘렸다" 판정 → payload·LawRef에 `article_text`(조문 전체) 추가, 답변은 전체 본문 사용(citation snippet은 짧게 유지).

### 검증
- E2E: 하이브리드→reranker(제2조 1위)→전체본문→**GPT 정확한 거실 정의 + `[[cite]]` PASS**.
- 단위 **47 통과**(rerank-before-dedup·article_text·dedup·citation id 회귀 포함).

### 일치율 (v4)
- FR-04 하이브리드 완성 + FR-05 reranker 완성 → v1 슬라이스 in-scope(FR-01·04·05·06·07·11) **전부 구현**.
- **정정 일치율 ~85% → ~95%** (Recall NFR을 실제 측정 가능한 상태로 전환. 정식 골든셋 측정은 후속).

### 잔여 (슬라이스6+ 게이트, 누락 아님)
as-of(D-2) · 위임그래프(delegatesTo S7) · RunService+agent교체(S8, 인용 프로덕션 주체) · testcontainers CI · 정식 Recall 골든셋.

---

## v5 — v4 교차검증(3관점 재가동) + 정직성 정정

v4(하이브리드+reranker)를 3관점 재검 → 기능 견고 확인, 단 ~95%가 과대로 판명 → ~90%로 정정 + 이탈 등재.

### 재검 핵심 (실행 증거)
- **하이브리드 실효 확인(런타임)**: dense가 못 찾던 조문을 sparse가 후보로 유입(건폐율→제45조, 방화구획→제52조의5, 지하층거실→제2조), reranker가 정밀도 — **recall↑/precision 역할분담 실관찰**. 기능·멱등·dedup·엣지·citation 전부 PASS.
- **테스트 High 갭 해소**: v4 간판경로(sparse upsert·dense/sparse prefetch RRF)가 단위·integration 어디서도 미실행이던 것 → `test_qdrant_hybrid.py`(fake client) + `test_embedding_flag.py`(sparse 변환) + integration 라이브 RRF 케이스 추가. **단위 54 통과**.

### v5 정정 (Major 반영)
| 항목 | 정정 |
|------|------|
| **일치율** | ~95% → **~90%**: 기능 in-scope(FR-01·04·05·06·07·11) 골격 전부 구현은 사실이나 ① **Recall NFR 미측정**(골든셋 미구현, N=1 E2E를 일반화한 부풀림) ② 임베딩 배포 이탈 미차감. reranker 1건으로 85→95 점프는 과도 |
| **Recall@10≥0.9 (DoD)** | "후속 백로그" → **v1 DoD 미충족 항목으로 격상**. 측정가능≠측정. 정식 골든셋 필요 |
| **인프로세스 임베딩 이탈** | Design §2.1/§6.2/§12는 **self-host FlagEmbedding 서버**. 구현 기본은 **in-process**(워커당 ~2.5GB RSS·6~10s 콜드스타트). → **이탈 등재**. 결정: **v1=in-process(슬라이스 검증용), production=remote/서버**(EMBEDDING_BACKEND=remote 폴백 존재). Design 도식은 "v1 in-process / prod 서버" 2모드로 갱신 필요 |
| **reranker=정확도 필수** | "선택(없으면 생략)" 표기 → **정확도 필수**로 격상. RERANK=0 폴백은 정의형 질의 악화(하이브리드 단독 약점). Design §8 Error Handling에 반영 필요 |
| storage | article_text가 분할 서브청크마다 중복저장 → 백로그(전체적재 시 누적) |
| §5 | dedup·rerank-before-dedup이 §5 의사코드에 미반영 → 갱신 필요 |

### 최종 판정 (v5)
**db-layer v1 Check: 기능 골격 완성(~90%), 단 Recall NFR 미측정·임베딩 배포 이탈로 "완전 통과" 아님.** 하이브리드는 실측으로 recall 향상이 입증됐고(긍정), 신규 경로 테스트도 닫혔다. **production/슬라이스6+ 전 게이트**: ① Recall 골든셋 측정(DoD) ② 임베딩 self-host 서버화(or prod=remote 확정) + 모델공유/워밍업/용량계획 ③ reranker 필수화·폴백 품질 명시 ④ as-of/위임/RunService(S7~8).

---

## v6 — 슬라이스7: 위임그래프(FR-02) 구현

### 구현
- **delegatesTo 위임그래프 ✅(FR-02)**: 소스 = law.go.kr **lsStmd(법령체계도) API**. `parse_hierarchy`가 base 법령의 직계 시행령/시행규칙(법령ID 보유·이름 prefix 일치)을 추출, 자치법규/조례(법령ID 없음) 제외. `build_delegation`이 `lo:delegatesTo(child→base)`를 named graph로 멱등 적재.
- **expand 의미 정합**: delegatesTo는 법령(resource)간 관계 → RetrievalService가 조문 payload의 `resource_id`로 법령 IRI를 만들어 expand(다단계 불필요, 조문→법령 매핑이 payload에 존재).
- **소스 피벗(설계 정정)**: FR-02 Design은 "AI-Hub 상위법령"이었으나 **AI-Hub(2018 부분집합)는 건축법 미커버** → "본문=law.go.kr API" 피벗과 일관되게 **lsStmd 채택**. lsStmd는 현행·권위적 법령ID 제공이라 이름매칭 모호도 없음(검증 게이트는 base 법령 resolve_current로 충족).

### 검증 (E2E)
- build_delegation("건축법") → delegatesTo(시행령 002118→건축법 001823, 시행규칙 006191→건축법) 2엣지.
- 건축법 시행령 적재(389청크) 후 "용도변경" 질의 → top에 건축법+시행령 조문 혼합 → **관계확장에 `002118 --delegatesTo--> 001823` 출현**.
- expand 방향성 정확: 시행령→건축법(outgoing) 매칭, 건축법 outgoing 없음.
- 단위 **55 통과**(`parse_hierarchy`: 직계 시행령/규칙만·조례/base 제외).

### 잔여 (슬라이스8 — 별 사이클)
- **RunService(agent updates→SSE 8종) + agent 도구 RetrievalService 교체(FR-12)** — agent 패키지 리팩터(src→agent_app·_flatten 이전 C-3) + conversation-store(transcript 영속, Design 미작성) 합류.
- as-of(D-2 select named-graph), Recall 골든셋, 임베딩 서버화는 v5 게이트 유지.

---

## v7 — 슬라이스7 교차검증 + 커버리지 수정

2관점(런타임·아키텍처) 재검 → **Critical 커버리지 갭 발견·수정**.

### 발견 (수치)
- **Critical 커버리지 갭**: `parse_hierarchy`가 `name.startswith(base_name)` 휴리스틱이라 lsStmd 트리에 구조적으로 명시된 위임 부령을 이름만으로 누락 — **건축법 8개 중 2개만(6개 75% 누락)**, 민법 29개 중 2개(93% 누락). 누락 부령(구조기준·설비기준·피난방화 규칙 등)은 채택된 시행규칙과 **동일 트리 경로 형제**.
- Design 본문(§7.2·§2.2·Summary·A-2)이 AI-Hub인 채 방치 → 구현(lsStmd)과 모순.
- build_delegation·expand-on-resource 자동테스트 부재.
- FR-03: child 무검증(lsStmd 권위성으로 child 모호게이트는 정당 불요, 단 현행여부 검증·격리리포트 미구현).

### 수정 (Act)
- **`parse_hierarchy` 구조 기반으로 교체**: 이름 prefix 폐기 → 상하위법 트리의 **법령ID 보유 노드(법종≠법률)** 전부 = 위임 하위. → **건축법 2→8 엣지**(누락 6개 회복), 자치법규(법령ID 없음) 자동 제외. precision 유지(엉뚱연결 0)하며 recall 대폭 향상.
- **테스트 추가**: `test_parse_hierarchy_children`(구조 기반·prefix 불일치 부령 포함), `test_delegation`(build_delegation 커버리지·delegatesTo child→base 방향·named graph). **57 통과**.
- **Design 본문 갱신**: §7.2 의사코드·§2.2를 lsStmd 기반으로, §5 expand를 resource_iri 기준으로 정정.
- expand-on-resource는 의미상 정확(delegatesTo=법령간 관계)임을 확인·문서화.

### 잔여 (정직)
- **FR-03 minor**: child 현행여부 검증·격리 리포트 미구현(lsStmd 현행 트리라 위험 낮음) — 백로그.
- 상법 등 base 동명 모호 시 resolve_current가 RuntimeError(precision 보수성 부작용) — 별칭/ID 직접지정 옵션 백로그.
- §5 의사코드가 코드 변화(dedup·rerank-before-dedup·resource expand)를 계속 뒤따라옴 — Design §5 일괄 정합 필요.

### 판정 (v7)
**슬라이스7 FR-02: 커버리지 수정 후 위임 하위 전부 포착(법령 한정, 자치법규 제외=1차 스코프) — 정직히 충족.** 메커니즘(방향·멱등·retrieval 통합)은 런타임상 견고. child 현행검증은 minor 백로그.

---

## v8 — 다른 관점 교차검증 (보안·SRE·법률도메인)

correctness 라운드가 못 본 차원. 대부분 **v1 수직슬라이스 범위 밖 게이트**(숨기지 않고 명시), 실제 수정 1·실버그 문서화 1.

### 즉시 수정 (Act)
- **delegatesTo 양방향 expand ✅**: 기존 outgoing만 → 상위 법령에서 **하위 위임법령(시행령/규칙) 조회 불가**(법률검증자 지적). expand를 노드가 주어/목적어 모두 매칭하도록 수정 → 건축법에서 하위 8건 회수. integration 테스트 양방향 검증. **57 통과**.

### 🔴 실버그 (다버전=슬라이스6 영역, 날카롭게 경고)
- **재적재 stale**: `lawgo_ingest`가 `is_current=True` 하드코딩, 구 expression 강등 로직 없음. **개정본 재적재 시 구·신 둘 다 `is_current=True` 공존 → 폐지 조문이 현행으로 인용될 수 있음.** v1은 단일 현행 1회 적재라 잠복. **슬라이스6(as-of/다버전) 진입 시 첫 개정에서 발현 → 그 전 필수**: 동일 resource_id 구 expression `is_current=False` supersede.

### 슬라이스8 게이트 (답변 생성 계층 — RetrievalService는 GPT 미호출)
- **면책 고지 서버측 강제**(Critical, 법률): 백엔드 답변에 "법률자문 아님·전문가 확인" 전무(FE 위젯 plan에만). 답변 생성 계층(agent/RunService)에서 후처리 강제.
- **cite 위조 검증 production화**(보안 Critical): `cited ⊆ ctx_ids` 검증이 E2E 스크립트에만 — 실제 답변 경로에 없음. RunService/agent에 게이트.
- **답변↔근거 정합 검증**(법률 High): 조문 부정확 요약(can→must 왜곡)·무인용 문장 탐지.
- **버전 고지·법/령 정합**(법률 High): 답변 머리에 기준 시행일자, 질의 법령명↔top-1 statute 정합(건축법 제2조 vs 시행령 제2조 혼동 차단).
- 인용 입도: 조 단위 → 항/호 명시 지시(당장)·세분 청크(로드맵).

### Production 하드닝 게이트 (별 phase)
- **서비스 형태 부재**(SRE Critical): backend/api·automation·inspector 빈 골격 — HTTP API 없음(RetrievalService=라이브러리, E2E 스크립트만 호출).
- **복원력 0**(SRE Critical): retry/circuit/fallback 없음, expand 실패가 요청 전체 500(부가정보인데). 관측성(로그/메트릭/트레이싱) 0.
- **보안**(보안 High): Qdrant 무인증(read+write 개방, live확인), law.go.kr **평문 HTTP→RDF/GPT**(MITM), admin123 하드코딩, select() template 미검증·GSP graph_uri 미검증.
- 백업/복구 0, 헬스체크·resource limit 0, in-process 모델 멀티워커 스케일(→모델 서비스 분리), article_text 중복저장.

### 정직한 재프레이밍 (핵심)
**v1 = 검증된 수직슬라이스(correctness ~90%) ≠ production-ready ≠ 법률실무 안전.** 보안·SRE·법률안전은 슬라이스가 다루지 않은 별 차원이며, 위에 **게이트로 전부 열거**(false confidence 방지). 슬라이스8(답변계층)·production phase에서 닫아야 공개 가능. 긍정: as-of/인젝션은 명확실패(NotImplementedError/ValueError), 멱등 적재, 타입드 바인딩, 양방향 위임은 견고.

---

## 슬라이스8 Check — FR-08 RunService + FR-12 도구 교체 (2026-06-25)

> **대상**: 슬라이스8만 (1~7은 위 v1~v8). **방법**: gap-detector(설계↔구현) + 런타임 적대(mutation) 2관점 교차검증. **결정**: RunService=인메모리 SSE 스켈레톤(영속·seq DB·approval 재개는 conversation-store Do 위임).

### 구현 매핑 (계약별)
| 설계 계약 | 권위 | 상태 | 근거 |
|-----------|------|:----:|------|
| C-2 도구 `content_and_artifact` (텍스트+`[id:]`라벨, AnswerContext) | db-layer §6.3 | ✅ | `agent/src/tools/legal.py`, `legal_core/retrieval.py:format_for_llm` |
| SSE 8종 이벤트 타입 방출 | CS §5 | ✅(7 실동작/1 신호) | `backend_app/services/run_service.py` |
| citation.added ← ToolMessage.artifact(LawRef[]) | CS §5·§6.2 | ✅ | id≡point.id 단일규칙(`ids.point_id`) |
| §5.1 cite 위조검증(본문⊆방출 id, 위반=error) | CS §5.1 C-b | ✅ 양방향 강제 | run_service `_finalize` |
| §5.1 면책·버전(시행일자) 머리고지 서버측 강제 | CS §5.1 | ✅ | run_service `_compose` |
| C-3 `_flatten_article`→legal_core 단일소스·역의존 제거 | §9.1 | ✅ | `legal_core/lawgo.py`, `law.py` 위임 |
| 역할경계: 검색=RetrievalService 단독/초안=agent/최종=RunService | §6.3 | ✅ | 분리 코드 실현 |

### v8 "슬라이스8 게이트" 종결 확인
v8이 열거한 답변계층 Critical/High 게이트 ↔ 이번 구현:
- **면책 고지 서버측 강제**(Critical) → ✅ RunService `_compose` 무조건 부착(프롬프트 비의존).
- **cite 위조 검증 production화**(보안 Critical) → ✅ E2E 스크립트→**실제 답변 경로(RunService)** 이전, 위반=기본 error.
- **버전 고지**(법률 High) → ✅ 인용 조문 eff_date 머리 부착.
- **답변↔근거 정합(의미 검증: can→must 왜곡·무인용 문장)**(법률 High) → ⚠️ **부분**: 구문적 cite 존재/위조는 강제하나 **의미적 정합(요약 왜곡 탐지)은 미구현** — 잔여 백로그.
- **인용 입도(조→항/호)** → 백로그 유지.

### 의도적 보류 (설계 명시 — 누락 아님)
transcript/citations DB 영속 · seq DB 채번 · 재연결 dedup · approval(`interrupt→Command(resume)`) 재개 · fork/reconcile/고아run/409 · message.delta → **전부 conversation-store Do 소유**(§12-8·CS §11). run_service docstring에 귀속 명시. 분모 포함 시 ~55%지만 슬라이스8 평가엔 제외(경계는 v1~v8과 동일).

### 런타임 적대(mutation) 결과
- 28건 **theater 아님**: DISCLAIMER 삭제·위조검사 무력화·format 라벨누출 mutation 전부 **잡힘**(FAIL). cite 위조검증은 정상id 통과+위조id 차단 **양방향**·대소문자/공백 변형도 위조로 차단(우회 불가).
- **노출된 테스트 사각 2건(코드는 옳으나 회귀 무감지)** → **즉시 보강(Act)**:
  - M4 `_dedup_by_article` max-score 선택: (저,고) 역순 픽스처 회귀게이트 추가 `test_dedup_keeps_max_score_not_first`.
  - M5 citation.added 동일id dedup: 단일 artifact 중복id 케이스 `test_citation_added_dedups_same_id_within_artifact`.
  - **재실행 30 통과**(legal_core+backend).

### 잔여 (슬라이스8 범위 내 Minor)
- ToolMessage 판별=클래스명 문자열(덕타이핑 의도적이나 래핑 시 취약) · 최종 AIMessage="tool_calls 없는 최신" 휴리스틱 · 빈 `[[cite:]]` 무음 처리(관측성 갭) · approval.requested 경로 무테스트. 전부 인메모리 스켈레톤 한계 내, conversation-store Do에서 Repository 교체 시 정리.

### 판정 (슬라이스8)
**일치율 ~96%(스켈레톤 약속 범위) · Critical/Major 진짜갭 0 · Check 통과.** 적대검증으로 노출된 사각 2건은 같은 세션에서 회귀게이트로 영속(검증자산 레포 영속 규율). 의미적 답변정합은 명시 백로그.

### 슬라이스8 심화 — 다각도 적대 교차검증 (3관점 추가)
앞선 2관점(gap-detector·mutation)이 **전부 사람이 만든 페이크 stream**에 의존한 한계를 깨기 위해 겹치지 않는 3관점 추가 투입.

| 관점 | 방법 | 핵심 결과 |
|------|------|-----------|
| 통합 현실성 | **실제 LangGraph**(langgraph 1.2.6 + langchain-core 1.4.8) `create_react_agent`+`build_react_graph`를 스텁 ChatModel(서버 불요)로 구동→RunService | 가정 대부분 실측 확인(노드명 agent/tools·tool_calls 구조·**ToolNode가 artifact 보존**·8종 순서·forgery error). **진짜 버그 1건 발견** |
| 보안 공격면 | PoC 실행(sse_wire/RunService 실호출) | SSE data 인젝션 불가(json.dumps 차단)·cite-id 위조 불가(결정적 UUIDv5) 확인. Med 3건 발견 |
| 아키텍처/의존성 | clean 프로세스별 import 실행 | 순환 import 0·§9.1 준수·이전 무결성(동일 객체)·DI 중복 0 — **위반 0** |

#### 발견→수정 (실제 경로로 수정 확인)
| 등급 | 발견(페이크가 못 잡음) | 수정 | 검증 |
|:----:|------------------------|------|------|
| 🔴 **HIGH** | **AIMessage.content가 콘텐츠블록 list**(`[{"type":"text",...}]`, Anthropic식 표준)면 `isinstance(str)` 가드가 본문 통째 누락 → message.completed=면책만·**cite 위조검증 무력화**(빈 본문). 실제 LangGraph에선 발현, 페이크(str)는 통과 | `_text_of()` 정규화(str/list 블록 모두 평문화) | edge_probe.py 실LangGraph 재실행: 본문·citations 보존 확인 |
| Med | _compose가 초안의 서버권위 줄(`⚖️ 기준 시행일자`·면책)을 strip 안 함 → LLM이 가짜 "공식 시행일자" 사칭 가능 | `_AUTHORITY_LINE` strip(권위 줄은 서버만 생성) | 회귀 `test_draft_cannot_spoof_server_authority_lines` |
| Med | 도구 오류 `f"...{exc}"`가 내부 엔드포인트/스택을 LLM·FE로 노출 | 사용자 대면 고정문구화(NotImplementedError만 사유 전달) | legal.py |
| Med(스펙) | `error` 뒤 `run.done` 방출 → FE 실패런 오인 | `_finalize`가 errored bool 반환, error는 terminal(run.done 억제) | e2e_falsify 위조경로 `…→error`로 종결 확인 |
| Low | `sse_wire` event名 필드 미이스케이프(현재 리터럴이라 미실현, 계약결함) | 개행/CR strip | — |

**재실행 33 통과**(legal_core+backend, +3 회귀). 수정 3건(HIGH·authority·error-terminal)은 **실제 LangGraph e2e로 재확인**(페이크 아님).

#### 잔여 백로그 (미수정, 명시)
- **의미적 답변정합**(can→must 왜곡·무인용 문장 탐지) — 구문 cite만 강제, 의미는 미검증.
- **프롬프트 인젝션**(법령 본문에 `[id:`/지시문 혼입 시 라벨 혼동·DoS) — forgery 검증이 가짜 cite의 사용자 도달은 차단하나, 본문 펜싱/sanitize는 미적용.
- **tool.result가 article_text 전문 방출**(snippet 200자 계약과 불일치) — 정책 결정 필요.
- **create_react_agent deprecation**(LangGraph V1→V2 제거 예정) → `langchain.agents.create_agent` 마이그레이션.
- **legal_infra eager import**(requests/qdrant/rdflib를 import 시점에 요구) — lazy 조립 의도와 별개, agent 런타임 설치 필요.

#### 심화 판정
**HIGH 버그 1건은 실제 LangGraph가 아니었으면 못 잡았을 사각**(페이크 30건 전부 통과했었음). 수정·실경로 재확인·회귀영속 완료. 아키텍처 위반 0. 잔여는 전부 백로그로 명시(false confidence 방지). **슬라이스8 Check 통과 유지(보강된 신뢰).**
