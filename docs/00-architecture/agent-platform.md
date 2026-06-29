# Agent Platform — Architecture Overview (north-star)

> **목적**: 흩어진 설계 결정을 하나로 모은 상위 아키텍처. 개별 feature 문서(FE Plan, db-layer Plan)를 묶는 인덱스이자 북극성.
>
> **Project**: 2026_06_20_Agent · **Version**: 0.1 · **Date**: 2026-06-24 · **Status**: Draft
> **원칙**: SoC · FSD · Zero-Trust · O(n) · 결정론 우선 · 최소 의존

---

## 1. 비전

**업무용 멀티 에이전트 플랫폼.** 분야별 전문 에이전트(법률·세무·건설기준 등)를 하나씩 추가해
나가는 함대(fleet). **법률 에이전트가 1호**이며, 같은 셸·엔진 위에서 도메인만 갈아끼운다.

핵심 통찰: 엔진이 ReAct 하나이므로 **"전문 에이전트" = 코드가 아니라 프로파일(데이터)**.

---

## 2. 핵심 원리 — 에이전트 = 프로파일

```
   공용 ReAct 엔진  ×  Profile{ prompt, tools[], retrieval, model, branding }  =  전문 에이전트

   새 분야 추가 = 프로파일 1개 (+ 새 도구/검색이 있으면 그것만)   ← 코드 변경 아님
```

- **프롬프트만이 아니다**: 진짜 전문화는 프롬프트 + **도구 세트** + **검색/DB 바인딩**까지 묶일 때.
  (법률 = 법률 프롬프트 + `search_legal` + GraphRAG 바인딩)
- 현재 `agent/src`의 `ReActAgent`가 사실상 **엔진 템플릿**, `get_tools(retrieval_service)`의
  의존성 주입(DI)이 **프로파일별 바인딩**의 씨앗이다.

### Agent Profile 스키마 (선언적)
```jsonc
{
  "id": "legal",
  "name": "법률 에이전트",
  "branding": {"icon": "scale", "accent": "blue"},
  "system_prompt": "…법령 인용 규칙 포함…",
  "tools": ["calculator", "search_legal"],
  "retrieval": {"type": "graphrag", "binding": "legal_core"},
  "model": {"provider": "openai", "name": "gpt-5.4-nano"},
  "widgets": ["delay_interest", "deadline"],
  "source_types": ["legal"],
  "routing": {"intents": ["법령","조문","판례","계약","처벌"]}
}
```
새 도메인 = 이 객체 하나 추가.

---

## 3. 시스템 구성 (루트 README 5계층 매핑)

| 계층 | 기술 | 이 플랫폼에서의 역할 |
|------|------|---------------------|
| `frontend/` | Svelte5(runes)·SvelteKit·FSD | 공용 대화 셸 + **라우팅/핸드오프 투명성** + 도메인 플러그인(출처/위젯 렌더러) |
| `backend/` | FastAPI | api·**AgentRunner**(stream→SSE)·**Supervisor/Router**·**ProfileRegistry**·Conversation·Citation 서비스 |
| `agent/` | LangGraph ReAct | 공용 엔진 + 프로파일 적용 + supervisor/handoff 그래프 |
| `database/` | relational + vector + graph | repositories 뒤에 은닉 |
| `db-admin/` | ingestion | zip→정제→적재 파이프라인 |

### 의존 방향 (db-layer의 "모순"은 DI로 해소)
```
backend.services ──invoke──▶ agent.engine        (런 오케스트레이션)
agent.tools ──(주입된 인터페이스)──▶ retrieval/repositories   (DI, 순환 아님)
frontend ──REST/SSE──▶ backend
```
> 코드의 `make_legal_tool(retrieval_service)`가 이미 **인터페이스 주입**이라, "agent가 backend를
> 부른다 vs backend가 agent를 부른다"의 외형적 순환이 **DI로 깨진다.** 도구는 API가 아니라
> *주입된 retrieval 인터페이스*에 의존한다. → 루트 README와 db-layer FR-08/§6.3을 이 문장으로 정합.

---

## 4. 런타임 — supervisor 라우팅 + handoff

선택: **자동 라우팅(supervisor) + 핸드오프**. LangGraph 멀티에이전트 그래프.

```
 사용자 질문
     │
     ▼
 ┌────────────┐   결정론 우선(capability 맵)
 │ Supervisor │── 적합 프로파일 선택 ──▶ ┌──────────────┐
 │  (Router)  │                          │ Specialist   │ = 공용 엔진 + 프로파일
 └────────────┘ ◀── handoff(Command) ──  │ (ReAct loop) │
        ▲         max_handoffs 가드        └──────────────┘
        └────────────── 핑퐁 방지 ───────────────┘
```

- **Supervisor의 두뇌 = 앞서 설계한 capability 맵/라우터.** LLM 분류보다 **결정론 맵 우선**(저비용·재현).
- **Handoff**: 전문가/슈퍼바이저가 `Command`로 제어를 다른 프로파일에 위임(법률→세무). **`max_handoffs` 가드**로 무한 핑퐁 차단.
- **라우팅 투명성(Trust)**: FE에 *라우팅 셀* — "법률 에이전트 선택 · 근거: 법령 질의", 핸드오프 표시.
  자동이 기본, **수동 오버라이드**(사용자가 에이전트 지정) 제공.

---

## 5. 컴포넌트 명세 (요약)

| 컴포넌트 | 위치 | 책임 |
|---|---|---|
| ProfileRegistry | backend | 프로파일 로드·조회(설정/DB) |
| Supervisor/Router | backend.services | 질문→프로파일(결정론 맵), 핸드오프 결정 |
| AgentRunner | backend.services | 엔진 `stream` → §6 SSE 이벤트 번역 |
| Shared ReAct Engine | agent | 프로파일로 파라미터화된 ReAct 루프 |
| Tool Registry | agent.tools | 공용 도구 + 프로파일별 subset, retrieval DI |
| Conversation/Citation Svc | backend.services | 스레드·메시지·근거(누적·중복제거) |
| 공용 셸 + 플러그인 | frontend | 대화·렌더·인용·위젯(범용) + 도메인 어댑터 |

---

## 6. 백엔드 프로토콜 (REST + SSE) — 통합

명령(REST)·이벤트(SSE)는 FE Plan §4 기준. 멀티에이전트로 **추가**되는 것:

| 추가 event | payload | UI |
|---|---|---|
| `routing.decided` | `{agent_id, reason, confidence}` | 라우팅 셀 |
| `handoff` | `{from, to, reason}` | 핸드오프 표시 |
| `plan.updated` | `{steps[]}` | Task 사이드바 계획 탭 (Codex) |
| `artifact.created` | `{id, kind, name, preview_url}` | 산출물 탭/뷰어 (Codex) |

명령 추가: `GET /agents`(프로파일), `GET /projects`, `GET /review-queue`·`POST /review/{id}/decision`(HITL), `GET /artifacts/{id}`(Codex), `POST /threads`에 `agent_id`(수동 지정).
기존: stream/tool/citation/approval/fork/summarize는 FE Plan §4와 동일.

---

## 7. 데이터 계층 (db-layer.plan.md 참조)

- **현재 결정(db-layer Plan)**: RDF 트리플스토어 **Fuseki** + 벡터 **Qdrant** + 임베딩 **BGE-m3**(self-host) + **리랭커**. GraphRAG: 하이브리드검색→리랭크→SPARQL 관계확장.
- **데이터 사실(분석 완료)**: `Agent_00/.../data` 2.6GB 중 ① 온톨로지·용어·관계는 **네이티브 RDF/N-Triples**(그래프 부트스트랩 가능, 단 2018 스냅샷) ② 법령·판례 원문 → 벡터 ③ 약 1.7GB는 **Instruction Tuning 학습용**(서빙 DB 아님).
- 세부·리스크·미해결은 §9 및 db-layer Plan.

---

## 8. 횡단 원칙 (모든 계층 공통)

- **SoC**: 계층/프로파일 경계 명확. FE는 표시·입력만.
- **Zero-Trust**: 승인 게이트는 **서버측**이 진짜 게이트(LangGraph interrupt). secret(LLM키·OC)은 BE/agent env만. 출력 렌더는 **새니타이즈**(raw HTML 금지), 위젯은 **선언적 카탈로그**(모델 JS 실행 0).
- **O(n)**: transcript append-only·윈도잉, 후보 cap 라우팅.
- **결정론 우선**: 라우팅·인덱스는 LLM 전에 결정론으로.
- **최소 의존·FSD**: FE 무빌드 스타일 토큰, 도메인=플러그인.

---

## 9. 결정 로그 (통합)

| 영역 | 결정 | 비고 |
|---|---|---|
| 플랫폼 | 멀티 에이전트 함대, 법률=1호 | 분야별 1개씩 |
| 전문화 | **프로파일(prompt+tools+retrieval)** 교체, 엔진 공용 | 코드 아님 |
| 라우팅 | **supervisor 자동 + 수동 오버라이드** | 결정론 맵 우선 |
| 협업 | **handoff 필요**, `max_handoffs` 가드 | LangGraph |
| FE 빌드 | SvelteKit(static) | FE Plan D1 |
| 스트리밍 | SSE + REST POST | D2 |
| 스타일 | Vanilla CSS + 토큰 | D3 |
| 상태 | Svelte5 runes | D4 |
| 근거 | 세션 누적(타입드 출처 패널, 범용화) | D6 |
| 메시지 수정 | 분기(fork) | D7 |
| 긴 대화 | 자동 요약 + 토글 | D8 |
| 출력 | Markdown+수식+지시자, raw HTML 금지 | D9 |
| 위젯 | 선언적 카탈로그(도메인별), MVP 포함 | D10 |
| **FE 레퍼런스** | **Codex 데스크탑 앱 벤치마크** — 커맨드 센터·Task 사이드바·Artifact viewer·리뷰 큐·프로젝트 조직·비동기 재개 | codex-reference §4.5 |
| LLM | OpenAI/호환 이중 지원 | config 구현됨 |
| 데이터 | Fuseki+Qdrant+BGE-m3+리랭커 | db-layer Plan |

---

## 10. 미해결 / 다음 결정

1. **db-layer Plan 개정**(검토 지적): ① 기존 RDF 코퍼스 반영(부트스트랩 vs law.go.kr 현행 이원화) ② "RDF 필수" 근거를 "데이터가 RDF+ELI"로 정정(OWL 추론 미사용) ③ 슬라이스 경량화 옵션 ④ FE 인용 스키마(`LawRef`↔`citation`) 정렬 ⑤ GPU(4090) 용량 가정.
2. **Supervisor 구현**: 결정론 capability 맵 vs LLM 분류기(또는 하이브리드).
3. **프로파일 저장**: 설정 파일 vs DB(런타임 추가 가능성).
4. **예상 도메인 목록**: 법률 외 어떤 분야(세무·건설기준·인사 등) — 라우팅·출처타입·위젯 카탈로그 폭 결정.

---

## 11. 로드맵 (점진)

| 단계 | 내용 |
|---|---|
| P0 | 단일 프로파일(법률) on 공용 엔진 — *대부분 코드 존재* |
| P1 | ProfileRegistry + 2번째 프로파일 |
| P2 | Supervisor 라우팅(결정론 맵) + `routing.decided` |
| P3 | Handoff(가드) + `handoff` 이벤트 |
| P4 | FE 공용 셸 + 라우팅/핸드오프 투명성(FE Plan 마일스톤) |
| P5 | db-layer GraphRAG 수직 슬라이스(db-layer Plan) |

---

## 12. 문서 맵 (index)

- **(본 문서)** `docs/00-architecture/agent-platform.md` — 상위 아키텍처·결정 로그
- `frontend/docs/pdca-plan-frontend.md` — FE 상세 Plan(멀티턴·리치렌더·위젯)
- `frontend/docs/codex-reference.md` — FE 설계 참고(Codex 표면 구조)
- `docs/01-plan/features/db-layer.plan.md` — 데이터 계층 GraphRAG Plan
- *(예정)* `docs/02-design/features/db-layer.design.md`, `backend.plan.md`, `agent-platform.design.md`
