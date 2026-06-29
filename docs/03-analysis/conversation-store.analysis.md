# conversation-store Gap 분석 (PDCA Check)

> **대상**: conversation-store **슬라이스1~5 + CORS + interrupt** · **기준**: Design v0.3 + Do/Do-XV1~XV12/Do-13 + FE 계약 §4.1/§4.2 · **분석일**: 2026-06-26 (**v3**)
> **방법**: gap-detector + **13+ 라운드 다각도 적대 교차검증**(실 LangGraph·라이브 PG·실 uvicorn SIGKILL·실 qwen/GPT LLM·라이브 Qdrant/Fuseki·홀리스틱 보안). 의도적 후속/게이트와 진짜 누락 구분.

## ⟦v3 — 슬라이스1~5 + CORS + interrupt Check⟧ (최신)

### 결과 요약 (v3)
| 지표 | 값 |
|------|-----|
| Match Rate (슬라이스1~5 분모) | **~97%** ✅ |
| FE 계약 정합 (§4.1 REST 9/9 · §4.2 SSE 8/8) | **100%** |
| 불변식·견고성 (Version History 주장 ↔ 실코드) | **100%** (12/12 확인) |
| 설계-코드 모순 | **0건** |
| 진짜 누락(v1 범위 내) | **0** |
| **판정** | **PASS → report** |

> **분모 정의**: 슬라이스1~5(v1 범위)=100% 분모. 슬라이스6·G1~G6·Last-Event-ID는 설계 §12가 명시한 **의도적 보류**라 분모 제외(포함 시 명목 ~78%이나 설계가 v1 범위 밖으로 선언).

### 슬라이스별 완성도 (v3)
| 슬라이스 | 핵심 | 상태 | 근거 |
|---------|------|:----:|------|
| 1 스키마+PostgresSaver+DI | enum·테이블·partial index·checkpointer 외부주입 | ✅ | `0001`·`db.py`·`container.py:37-46`·`agent.py:33` |
| 2 ConversationRepository | seq원자·active-run 409·citation 동결·fork CTE | ✅ | `conversation.py` (fork cut 버그 수정 반영) |
| 3 RunService updates→SSE 8종 | 8종 변환·커밋후방출·cite검증·면책·버전 | ✅ | `run_service.py` |
| 4 승인 interrupt+reconcile | interrupt_before·resume·CAS·reconcile·TTL·tool_call_id | ✅ | `agent.py`·`run_service.py`·`conversation.py`·`0002` |
| 5 합성루트+핵심 API+디커플 | lifespan 부팅시퀀스·run↔stream 디커플 | ✅ | `app.py:47-72`·`run_manager.py` |
| 5b fork F-1 | 참조분기+checkpoint state 시드 | ✅ | `app.py`·`run_service.py`·`agent.py` |
| 5c settings/models+parent_id | settings CRUD·models·턴 루트 그룹핑 | ✅ | `app.py`·`conversation.py` |
| 5d summarize | LLM 직접호출·범위요약·threadpool 오프로드 | ✅ | `app.py`·`run_service.py`·`agent.py` |
| **+CORS** | CORSMiddleware·CORS_ORIGINS env·실 SSE 헤더 | ✅ | `app.py:75-92` |
| **+interrupt** | 협조취소·'interrupted' 터미널·thread 잠금해제 | ✅ | `run_service.py:259-263`·`run_manager.py`·`0003` |
| 5 잔여 Last-Event-ID | transcript 기반 재연결 | 🔵 후속(G4) | `run_manager.py:14-15` 주석 |
| 6 MemoryProvider | 후속 슬라이스 | 🔵 후속 | §11 `[ ]6` |

### Gap 목록 (v3)
| 분류 | 건수 | 내용 |
|------|:----:|------|
| 🔴 Missing(설계O 구현X) | **0** | v1 범위 내 누락 없음 |
| 🟡 Added(설계X 구현O) | 3 (정당) | `timeout_stale_runs`(고아 안전망)·`_synthetic_terminal`(hang방지)·`list_threads`(FE B5 정합) |
| 🔵 Changed(설계≠구현) | 3 (수렴) | cite 위조검증 per-answer→**thread 스코프**(Do-XV7)·approval payload `{id,run_id,action,detail}`(FE정합)·stream 경로 `/runs/{run_id}/stream`(FE B1) — **전부 설계 문서가 코드 기준 갱신됨**, 모순 아님 |
| 🟠 Partial | 1 (후속) | timeout sweep **heartbeat 부재**(§12 G5 명시) — 디커플로 고아 run 구조적 해소돼 sweep 의존도↓, 심각도 낮음 |

### 불변식 검증 (12/12 실코드 확인)
cite 위조 thread스코프(`run_service.py:363`)·마커 공백정규화(`:358`)·CAS 종결(`:286`)·user NUL보호(`app.py:214`+`run_service.py:182`)·summarize threadpool(`app.py:151`)·CORS(`app.py:75`)·협조취소+interrupted 터미널(`run_service.py:259`+`0003`)·권위줄 strip(`:385`)·artifact dict 명시실패(`:113`)·seq+message 원자(`conversation.py:167`)·reconcile awaiting 제외(`:27`)·첫동결 ON CONFLICT(`:319`).

### 의도적 보류 (갭이지만 누락 아님)
슬라이스6 MemoryProvider · Last-Event-ID(G4) · G1 인증/IDOR(2026-06-26 연기 결정) · G2 XSS살균(FE) · G3 rate-limit · G4 다중인스턴스 · G5 heartbeat/per-tool승인 · G6 observability/프롬프트펜싱.

### 판정·권고 (v3)
- **✅ Check 통과 (~97% ≥ 90%) → `/pdca report conversation-store`.**
- iterate 대상 없음(90% 미만 항목이 v1 범위에 없음). 후속 게이트(G1~G6·슬라이스6)는 별도 PDCA 사이클.

---

## ⟦v2 — 슬라이스1~5 Check⟧ (이전)

> 아래 v1.0은 슬라이스1~4 시점 기록.

## 결과 요약
| 지표 | 값 |
|------|-----|
| 슬라이스1~4 일치율 | **97%** (계약 35개: 완전 33 · 부분 1 · 의도적 1) |
| 진짜 누락 | **0** |
| Critical/Major 갭 | **0** |
| 교차검증서 수정된 실버그 | **4건**(전부 닫힘, 회귀 영속) |

## 구현 범위 (슬라이스1~4)
| 슬라이스 | 내용 | 상태 |
|---|---|---|
| 1 | PG 스키마(0001)·자체 Docker PG(:5434)·DI(풀/checkpointer/repo)·agent checkpointer 외부주입 | ✅ |
| 2 | ConversationRepository(seq 원자채번·citations 동결·message_citations·fork 재귀 ancestor-union) | ✅ |
| 3 | RunService 영속화(updates→SSE 8종·transcript·커밋후방출·최종본문 §5.1) | ✅ |
| 4 | 승인 interrupt(`interrupt_before`+resume)·awaiting_approval 영속·approve/reject(그래프 정리)·마이그0002 tool_call_id·reconcile(고아run·승인TTL) | ✅ |

## 설계 계약별 대조
| 설계 | 계약 | 상태 | 근거 |
|------|------|:----:|------|
| §3.2 | threads/runs/messages/citations/message_citations/summaries/settings + enum | ✅ | `0001_*.sql` ↔ 설계 DDL 1:1(컬럼·제약·인덱스) |
| §3.2 | active-run partial unique index(동시run 금지) | ✅ | `one_active_run_per_thread`, 409=ActiveRunExists |
| §3.3 | seq 원자채번 `UPDATE...RETURNING` | ✅ | `next_seq`/`add_message` tx 내부 원자(XV 갭 제거) |
| §3.4 | fork 참조모델·조상 ancestor-union·seq 연속성 시드 | ✅ | `fork_thread`·`get_thread_messages` 재귀 CTE(cut=자식 fork_point) |
| §4 | citation 방출순간 freeze(전문)·ON CONFLICT DO NOTHING·message_citations 링크 | ✅ | `freeze_citation`·`add_message(citation_ids)` |
| §5 | updates→SSE 8종 매핑·커밋후방출·seq DB채번 | ✅ | `RunService._drive`/`_handle_message`(영속 후 yield) |
| §5.1 | cite 위조검증(error 기본)·면책·버전고지 서버측강제 | ✅ | `_finalize`/`_compose`(권위줄 사칭 strip 포함, XV) |
| §6 | 승인 interrupt_before+resume·awaiting 영속·reject 그래프정리 | ✅ | agent `require_approval`/`resume`/`reject_pending`·RunService `resume`/CAS |
| §6 | reconcile(고아run→error)·승인 TTL | ✅ | `reconcile_orphan_runs`(awaiting 제외, XV)·`timeout_stale_approvals` |
| §6 | PostgresSaver 공존(checkpoint* 4테이블)·DI 외부주입 가능 | ✅ | T1 라이브 실증(이름충돌 0)·`build_checkpointer(pool)` |
| §6 | checkpointer 실주입(영속 활성화) | ⚠️**부분** | 주입 *능력* 있음(`ReActAgent(checkpointer=)`), 실배선은 합성루트=슬라이스5 |
| §8 | 동시run 409·고아 reconcile·승인 TTL·cite위조 error | ✅ | 위 + 라이브 테스트 |
| §5 | message.delta | ⊝**의도적** | updates 모드라 미지원(설계 명시, messages 모드 후속) |

## 다각도 적대 교차검증 (이미 수행, 실버그 4건 수정)
설계↔문서 정합을 넘어 **실 LangGraph + 라이브 PG**로 3라운드 적대 검증 — 페이크가 못 잡은 실버그를 실경로가 노출:
| # | 실버그 | 등급 | 수정 |
|---|--------|:---:|------|
| 1 | fork CTE 조상 cut을 자기 fork_point(NULL)서 계산 → 분기후 부모 메시지 누수 | High | 자식 `c.fork_point_message_id`서 계산 |
| 2 | PostgresSaver `CREATE INDEX CONCURRENTLY` autocommit 필요 | Med | `build_pool(autocommit=True)` |
| 3 | reconcile가 awaiting_approval(durable) 살해 → 부팅마다 미결승인 소실 | High | `RECONCILE_STATUSES`서 awaiting 제외(TTL 전용) |
| 4 | reject가 LangGraph 체크포인트 미정리 → 새 run INVALID_CHAT_HISTORY 영구잠금 | Med-High | `reject_pending`(RemoveMessage)로 next=() 초기화 |
+ seq 원자화·ORDER BY tiebreak·read id ::text·container lazy import·도구오류 비노출·CAS 동시 resume·서버권위줄 사칭 strip(슬라이스8 합류분 포함).

**검증 자산(레포 영속)**: 단위 38(+3 skip) · 라이브 PG 통합 16(2회 멱등) · realgraph e2e exit0. 실 LangGraph interrupt→resume·동시 CAS(N=16 winner==1)·reject-재사용 회귀 포함.

## 의도적 보류 (슬라이스5~6 — 누락 아님)
설계 §11이 명시:
- **슬라이스5**: fork **state-values 시드(F-1)**·summarize·이력복원·FE §4.1 API · **합성루트(`api/`)·부팅시퀀스(`run_migrations`→`PostgresSaver.setup()`→`reconcile_orphan_runs`)·checkpointer 실주입**. 부품(함수)은 전부 구현·테스트, 빠진 건 부팅 시 순서 호출하는 `main`/`lifespan` 1곳.
- **슬라이스6**: MemoryProvider(③ 장기기억).
- `interrupted` 상태: enum 예약·미배선(무해). reconcile 전역쿼리: 단일-인스턴스 전제(주석 명시).

## 판정
**슬라이스1~4 Check 통과 (97%, Critical/Major 0).** 영속·seq·citation동결·fork·RunService 8종·승인 interrupt/resume·reconcile이 **실 LangGraph+라이브 PG로 견고 확인**, 적대검증 실버그 4건 닫힘. 미배선(합성루트·부팅시퀀스·checkpointer 실주입)은 **슬라이스5 경계**로 1~4 결함 아님.

**슬라이스5 Check 1순위**: 현재 기본 실행은 `agent.py` `build_checkpointer()` 폴백=MemorySaver(비영속). 슬라이스5 합성루트가 PostgresSaver 주입 + §8 "setup 미실행 헬스체크 강제"를 배선해야 영속이 실제 활성화. + 라이브 인프라 미가동 환경에선 통합 16건이 skip되므로 CI에 PG 서비스 필요.

---

## v2 — 슬라이스1~5 Check (XV3~5 종합, 2026-06-26)

> 위 v1.0(슬라이스1~4)에 **슬라이스5(API+합성루트+디커플)** 와 이후 교차검증 라운드(XV3 디커플·XV4 실 LLM 양 provider·XV5 크래시내구성+보안)를 합류. **방법**: 단일 gap-detector 패스 불신 — 10+ 라운드 다각도 적대(실 LangGraph·라이브 PG·실 uvicorn SIGKILL·실 qwen/GPT LLM·홀리스틱 보안)로 검증.

### 슬라이스5 구현 + Check
| 슬라이스 | 내용 | 상태 |
|---|---|---|
| 5 | 합성루트(lifespan 부팅: migrate→setup→reconcile→checkpointer **실주입**)·핵심 API(threads/messages/이력복원/citations/approve·409·404·검증)·**run↔stream 디커플**(RunManager: POST→run_id 즉시+백그라운드 완주+GET stream tail) | ✅ 핵심 / fork·summarize·settings·models·재연결 후속 |

→ v1.0이 "슬라이스5 Check 1순위"로 지목한 **배포 게이트(checkpointer 실주입·부팅시퀀스)가 슬라이스5에서 닫힘**.

### 교차검증 종합 (XV1~5) — 발견·수정한 실버그
페이크/스텁이 숨겼고 **실경로가 노출**한 버그를 라운드마다 수정·회귀영속:
| 라운드 | 발견(실경로) | 수정 |
|---|---|---|
| XV(1~4) | fork CTE 조상 cut·PostgresSaver autocommit·reconcile-awaiting 살해·reject 체크포인트 잠금 | (v1.0 4건) |
| XV(슬5) | 부활/이중run(무가드 종결)·승인 dead path(require_approval)·풀누수·이력 citation 미동봉 | CAS 종결·REQUIRE_APPROVAL env·try/finally·GET citations |
| XV3 디커플 | disconnect-고아·timeout-오살·stream hang·executor 고갈·shutdown 경합 | RunManager 디커플·합성terminal·전용풀·drain |
| XV4 실 LLM | 빈답(그라운딩실패)·config `LLM_TEMPERATURE=''` 크래시·OpenAI 키 silent 무시 | _NO_ANSWER·strip·명확에러 |
| XV5 | POST /threads owner_id 비-UUID→500 | UUID 검증 422 |

### 실경로로 입증된 핵심 계약 (스텁 아님)
- **실 LLM(qwen+GPT) 답변계층**: 도구호출·`[[cite:실id]]` 준수·**위조 안 함**(그라운딩 실패시 기권)·cite위조 가드·면책·버전·checkpoint·멀티턴·승인 interrupt→resume — **양 provider 통과**.
- **§1.1 "100% 복원"**: 실 uvicorn SIGKILL → 재부팅 reconcile·**awaiting_approval 생존→/approve 재개 완주**·transcript 일관 — 단일 인스턴스 성립.
- **보안**: SQLi 불가(파라미터화+UUID)·비밀누출/SSE인젝션/cite위조/권위사칭 차단.

### 잔여 (전부 의도적 후속/게이트 — 진짜 누락 0)
- **기능 후속(슬5)**: fork F-1·summarize·settings·models·parent_id 셀그룹핑·Last-Event-ID 재연결.
- **production 게이트(설계 §12 G1~G6)**: 🔴인증/인가(IDOR, **2026-06-26 의도적 연기**=신원소스와 함께)·FE 살균·rate-limit·다중인스턴스(reconcile liveness·공유버스)·timeout heartbeat·observability.
- 슬라이스6 MemoryProvider(③).

### 판정 (v2)
**슬라이스1~5 Check 통과 · Critical/Major 진짜갭 0 · 일치율 ~96%(슬5 핵심 범위).** 핵심 약속(영속·복원·답변계층)을 **실 LLM·실 크래시로 입증**, 실버그 다수 수정·회귀영속. 미해결은 전부 **설계 §12 게이트로 명시**(false confidence 방지). **v1 = 단일 인스턴스·신뢰 LLM·외부 비노출 전제**에서 검증됨 — G1~G6 닫기 전 공개 금지.

---

## Version History
| Version | Date | Changes |
|---------|------|---------|
| 1.0 | 2026-06-25 | 슬라이스1~4 Check — gap-detector 97% + 3라운드 적대 교차검증 종합, 의도적 보류(S5~6) 구분 |
| 2.0 | 2026-06-26 | 슬라이스1~5 Check — XV3~5(디커플·실LLM양provider·크래시내구성+보안) 종합. 배포게이트 닫힘·production게이트 §12 명시. ~96% |
