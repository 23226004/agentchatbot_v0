# PDCA Plan — Frontend (AI Agent Service) · v2

> **Feature**: `frontend` · **Phase**: `plan` · **갱신일**: 2026-06-24
> **스택**: Svelte 5 (runes) · SvelteKit(static) · FSD · SSE+REST
> **근거**: [`codex-reference.md`](./codex-reference.md), 루트/agent/backend README, 설계 논의(멀티턴·리치렌더·위젯)
> **원칙**: SoC · FSD · Zero-Trust · O(n) 렌더 · 최소 의존

---

## 0. 확정 결정 (Do 진입 전 사인오프)

| # | 결정 | 채택 | 근거 |
|---|------|------|------|
| D1 | 빌드 기반 | **SvelteKit + adapter-static (SPA)** | 표준·라우팅 내장·SSR 전환 여지 |
| D2 | 스트리밍 규약 | **SSE(이벤트) + REST POST(명령)** | ReAct 단계 스트리밍 최적·인프라 단순 |
| D3 | 스타일링 | **Vanilla CSS + 디자인 토큰 + scoped style** | 무의존·FSD 캡슐화·토큰 중앙화 |
| D4 | 상태 | **Svelte 5 runes** + `.svelte.ts` 스토어 | 요구사항 |
| D5 | MVP 범위 | **Full** (2장) | 멀티턴·리치렌더·위젯 포함 |
| D6 | 근거 패널 | **세션 누적 근거 라이브러리** (+ "이번 답변/전체" 탭) | 법률은 사건 단위로 근거가 쌓이는 게 산출물 |
| D7 | 메시지 수정 | **분기(fork)** — 원본 보존 + 가지치기 | "이 가정이면?" 분기 상담 잦음 |
| D8 | 긴 대화 | **자동 요약 + 수동 "요약하기" 토글** | 컨텍스트 윈도우 보호 |
| D9 | 출력 포맷 | **Markdown + 수식(`$…$`) + 지시자(`:::callout`, ` ```mermaid `, ` ```widget `, `[[cite:…]]`)**, raw HTML 금지 | 신뢰 경계: 모델 HTML 직접 실행 차단 |
| D10 | 인터랙티브 위젯 | **선언적 카탈로그(고정 컴포넌트), 모델은 타입+파라미터만, JS 실행 0** · **1차 포함** | 표현력↑, Zero-Trust 유지 |

> 위 표가 이 Plan의 전제다.

---

## 1. 목표 & 배경 (Why)

AI Agent 서비스의 **사용자 표면(surface)**. Codex 통찰대로 **FE는 엔진(agent)을 직접
호출하지 않고**, backend의 **이벤트 스트림 + 명령 제출** 규약만 소비한다. 도구에
`search_law`·`get_law_articles`가 붙어 있어 **법률 전문가형 + 도구 투명성** 페르소나를 채택한다.
신뢰·근거·정확성이 재미보다 우선이다.

---

## 2. 스코프 (Full MVP)

### In scope
1. **다중 대화 스레드** — 생성/전환/삭제, 사이드바.
2. **스트리밍 대화** — 단계(thinking→tool→writing) + 토큰 in-place.
3. **도구 호출 시각화** — `tool.call → tool.result` 접이식 셀(지난 턴 자동 접힘).
4. **멀티턴 흐름** (5장) — 누적 근거 라이브러리, 이전 턴 인용·이어묻기, 분기, 긴 대화 요약, 윈도잉.
5. **리치 콘텐츠 렌더링** (6장) — Markdown·표·수식(LaTeX)·Mermaid·콜아웃·접이식·PDF 임베드, 보안 새니타이즈.
6. **인터랙티브 위젯** (7장) — 위드마크·지연이자·기한 계산 등 선언적 위젯(값 입력 → 즉시 연산).
7. **승인 워크플로(Zero-Trust UX)** — 위험 동작 전 승인 모달.
8. **슬래시 커맨드** — `/model`, `/new`, `/clear`, `/summarize`.
9. **설정** — 모델 선택, 서버 URL, 테마(라이트/다크).
10. **이력/영속** — 스레드·메시지·근거 라이브러리 backend 영속·복원.

### Non-goals
자체 인증 시스템, 모바일 네이티브, 다국어(한국어 우선), 음성, 임의 HTML/JS 실행.

---

## 3. 아키텍처

### 3.1 표면 모델 (Codex protocol 차용)
```
 사용자 ─입력▶ features(send/approve/slash/edit) ─▶ shared/api(command: REST POST)
                                                          │
 backend(FastAPI) ◀────────────────────────────────────────┘
        │  SSE event stream
        ▼
 shared/api(event channel) ─▶ entities(message/thread/citation, runes) ─▶ widgets(render)
```
- 모든 backend 통신은 `shared/api`에만. 위젯/페이지는 HTTP·SSE를 모른다(SoC, 교체 격리).

### 3.2 FSD 레이어 매핑 (의존: 상위→하위만)
`app → pages → widgets → features → entities → shared`

| 레이어 | 책임 | 핵심 산출물 |
|--------|------|------------|
| `app/` | 전역 설정·프로바이더·테마·라우팅 진입 | 앱 셸, 토큰 주입, api 프로바이더 |
| `pages/` | 라우트 화면 조합 | `ChatPage`, `SettingsPage` |
| `widgets/` | 독립 UI 블록 | `ConversationView`, `Composer`, `ThreadSidebar`, `EvidencePanel`, `StatusBar` |
| `features/` | 사용자 행동 | `send-message`, `approve-action`, `slash-command`, `edit-fork`, `summarize-thread`, `select-model`, `manage-thread` |
| `entities/` | 엔티티 + 셀 | `message`(User/Agent/Tool 셀), `thread`, `citation`(근거), `session` |
| `shared/` | 규약·렌더·UI·유틸 | `api`(SSE/REST), `render`(md/math/mermaid/sanitize), `widgets`(위젯 카탈로그), `ui`(토큰·프리미티브) |

> SvelteKit `src/routes/`는 page를 조합만 하는 **얇은 진입점**. 도메인은 `src/lib/` FSD.

---

## 4. 백엔드 계약 (protocol) — 초안

### 4.1 명령 (REST POST)
| 경로 | 용도 |
|---|---|
| `POST /threads` · `GET /threads` · `GET /threads/{id}/messages` | 스레드/이력 |
| `POST /threads/{id}/messages` → `{run_id}` | 메시지 전송(실행 시작) |
| `GET /threads/{id}/stream?run_id=` (**SSE**) | 이벤트 수신 |
| `POST /runs/{id}/interrupt` · `POST /runs/{id}/approve` | 중지·승인 |
| `POST /messages/{id}/fork` → `{thread_id}` | 메시지 수정 시 분기(D7) |
| `POST /threads/{id}/summarize` | 긴 대화 요약(D8) |
| `GET /threads/{id}/citations` | 누적 근거 라이브러리(D6) |
| `GET /models` · `GET/PUT /settings` | 설정 |

### 4.2 이벤트 (SSE `event:` 타입)
| event | payload | UI 반응 |
|---|---|---|
| `run.started` | `{run_id, thread_id}` | 스피너 on, live 턴 표시 |
| `message.delta` | `{text}` | active 셀 in-place(O(n) tail) |
| `tool.call` / `tool.result` | `{id, name, args}` / `{id, content}` | 도구 셀 |
| `message.completed` | `{text, content_type:"markdown", citations:[id]}` | 셀 확정 + 렌더 + 근거 라이브러리 갱신 |
| `citation.added` | `{id, kind, title, ref, snippet, url}` | 근거 패널 누적(중복제거) |
| `approval.requested` | `{id, action, detail}` | 승인 모달 |
| `error` / `run.done` | `{message}` / `{run_id}` | 에러 셀 / 스피너 off |

> 메시지 본문 `content_type:"markdown"` 고정. 인용은 본문 내 `[[cite:<id>]]` → 근거 라이브러리 항목으로 해소.
> 위젯은 본문 ` ```widget ` 펜스로 표현(7장). agent가 토큰 스트림(`stream_mode="messages"`) 미지원이면 `message.delta` 생략하고 `message.completed`로 시작 — **Check 항목**.

---

## 5. 멀티턴 대화 흐름

### 5.1 트랜스크립트
- **스크롤**: 사용자가 바닥에 있을 때만 자동 추적, 위로 올리면 고정 + "↓ 최신으로" 버튼.
- **네비게이션**: 좌측 턴 앵커(질문 요약) 미니 목차, `Ctrl+↑/↓` 점프.
- **O(n)/윈도잉**: append-only + 가시 영역만 렌더, 확정된 옛 턴은 정적 경량 노드.

### 5.2 도구 셀·근거 누적 (D6)
- 지난 턴 도구 셀은 **자동 접힘**(한 줄 요약 칩), 현재 턴만 펼침.
- **누적 근거 라이브러리**: 스레드에서 인용한 법령/판례를 우측 패널에 중복제거·누적.
  패널 상단 탭 "이번 답변 / 전체". 인용 칩 클릭 → 해당 항목 점프·하이라이트.

### 5.3 맥락 연속성
- **이전 턴 인용·이어묻기**: 메시지/인용 칩을 끌어 다음 질문에 첨부(quote-reply).
- **컨텍스트 가시화**: 길어지면 "요약하기" 안내, 오래된 턴은 요약으로 압축(D8). "기억 중" 경계 표시.
- **메모리 출처 구분**: 장기 메모리(VectorDB)에서 끌어온 근거는 별도 라벨.

### 5.4 턴 단위 제어
- 메시지별 **복사 / 재생성 / 수정**. 수정 시 **분기(fork)**(D7) — 원본 보존, 분기 트리 네비.
- **live 턴은 항상 하나** — 스트리밍 중 입력 잠금 또는 큐잉.

### 5.5 실패·복구
- 중간 턴 도구 실패·SSE 끊김에도 트랜스크립트 무결. 해당 턴에 "실패·재시도" 인라인 셀, 그 턴만 재실행. SSE 자동 재연결 + `run_id/seq`로 중복 방지.

---

## 6. 리치 콘텐츠 렌더링 & 보안 경계

### 6.1 신뢰 경계 (Zero-Trust) — 최우선
- 모델 출력은 **신뢰 불가 입력**. 파이프라인: `agent(markdown) → parse → **DOMPurify 새니타이즈** → render`.
- **raw HTML 직접 실행 금지**(D9). HTML이 꼭 필요하면 allowlist 새니타이즈 또는 **샌드박스 iframe(CSP·스크립트 차단)**.
- **링크 안전**: 외부 링크 확인 후 열기, 법령 출처 도메인 allowlist.
- **폴백**: 렌더 실패·의심 시 "원본 보기"로 안전 노출.

### 6.2 렌더 파이프라인 (`shared/render`)
`markdown-it`(또는 marked) → DOMPurify → 후처리 변환:
- **수식**: KaTeX (`$…$` 인라인 / `$$…$$` 블록).
- **다이어그램**: Mermaid(지연 로드) — 소송 절차, **위임법령 트리**(`get_delegated_laws` 연동).
- **지시자**: `:::callout{type}`(핵심/주의/면책), 접이식(`details`), `[[cite:id]]`(근거 해소), ` ```widget `(7장).
- **표/코드**: GFM 표, 코드 하이라이트 + 복사 버튼.

### 6.3 스트리밍 점진 렌더
- 본문 텍스트는 즉시. **블록 요소(수식·Mermaid·표·위젯)는 펜스가 닫힐 때 지연 렌더** — 미완성 마크다운 깜빡임 방지.

### 6.4 부가 기능
- **원본/렌더 토글**, **복사(마크다운/플레인/코드)**.
- **선택→인용 질문**(5.3 연계).
- **내보내기**: 턴/스레드 → PDF·docx(근거 포함, 법률 산출물).
- 다크모드·접근성(수식 aria, 표 헤더).

---

## 7. 인터랙티브 위젯 (선언적·안전, MVP 포함 — D10)

### 7.1 안전 모델 (핵심)
- 모델은 **위젯 타입 + 파라미터만** 지정. **JS를 작성·실행하지 않는다.**
- FE는 **고정 카탈로그**의 신뢰된 Svelte 컴포넌트만 렌더. 파라미터는 **스키마 검증**(범위·타입). eval 없음.
- 표현(본문): ` ```widget ` 펜스에 JSON.
  ```
  ```widget
  {"type":"widget.delay_interest","params":{"principal":10000000,"rate":0.12,"from":"2024-01-01","to":"2025-06-01"}}
  ```
  ```
- 미지원 타입·검증 실패 → 렌더 안 하고 원본/경고로 폴백.

### 7.2 1차 카탈로그 (법률)
| 위젯 | 용도 | 파라미터(예) |
|---|---|---|
| `widget.bac_widmark` | 혈중알코올농도(위드마크) | 체중·성별·음주량·경과시간 |
| `widget.delay_interest` | 지연이자/지연손해금 | 원금·이율·기간 |
| `widget.deadline` | 기한 계산(소멸시효·항소기한 등) | 기산일·기간·기준 |
| `widget.penalty_range` | 벌금/양형 구간 안내 | 위반유형·구간 |

- 위젯 **계산 로직은 FE가 소유**(신뢰). 사용자가 값을 바꾸면 즉시 재계산.
- 각 위젯은 **면책 표기 + 근거 링크**(관련 조문) 동반.

### 7.3 확장
새 위젯 = `shared/widgets/`에 컴포넌트 추가 + 카탈로그 등록(개방-폐쇄). 모델 프롬프트에 사용가능 타입 주입.

---

## 8. Svelte 5 Runes 지침
- 로컬 `let x = $state()`, 파생 `$derived`/`$derived.by`, 효과/구독 `$effect`(SSE 정리 포함).
- props `$props`, 양방향 `$bindable`.
- **공유 스토어**: `.svelte.ts`에서 `$state` 객체/클래스 export (예: `citation/library.svelte.ts`).
- **O(n)**: transcript는 append-only, active 셀만 갱신, `{#each ...(id)}` 키 안정화.

---

## 9. FSD 산출물 (Do 파일 목록)
```
src/lib/
  shared/
    api/        client.ts · events.ts(SSE) · contracts.ts(4장 타입 단일 소스)
    render/     markdown.ts · sanitize.ts · math.ts(KaTeX) · mermaid.ts · directives.ts
    widgets/    registry.ts · schema.ts · BacWidmark.svelte · DelayInterest.svelte · Deadline.svelte · PenaltyRange.svelte
    ui/         tokens.css · Button/Modal/Spinner/Markdown(.svelte)
    lib/        utils · id · time
  entities/
    message/    types.ts · message-store.svelte.ts · UserCell/AgentCell/ToolCell.svelte
    thread/     thread-store.svelte.ts · types.ts (분기 트리 포함)
    citation/   library.svelte.ts(누적·중복제거) · types.ts · CitationChip.svelte
    session/    설정·연결 상태
  features/
    send-message/ · approve-action/ · slash-command/ · edit-fork/ · summarize-thread/ · select-model/ · manage-thread/
  widgets/
    ConversationView.svelte · Composer.svelte · ThreadSidebar.svelte · EvidencePanel.svelte · StatusBar.svelte
  pages/        ChatPage.svelte · SettingsPage.svelte
  app/          App.svelte · theme.ts · providers.ts
src/routes/     SvelteKit 얇은 진입점
```

---

## 10. 작업 분해 (마일스톤)
| M | 목표 | 수용 기준 |
|---|------|----------|
| **M0** | 스캐폴딩 | SvelteKit+static, FSD 폴더, 토큰, FSD 의존 린트, 빈 ChatPage |
| **M1** | 규약 계층 | `shared/api`(contracts/client/events)+entities, mock SSE 1건→셀 표시 |
| **M2** | 핵심 대화 | Composer+ConversationView+send-message, 스트리밍 in-place(O(n)) |
| **M3** | 리치 렌더 | `shared/render`(md/표/수식/Mermaid/콜아웃)+새니타이즈+점진 렌더+원본 토글 |
| **M4** | 위젯 | `shared/widgets` 카탈로그 4종+스키마 검증+면책/근거, ```widget 렌더 |
| **M5** | 도구·승인 | ToolCell(접이식)+approve-action 모달→/approve |
| **M6** | 멀티턴 | 누적 근거 라이브러리+턴 앵커+자동스크롤+윈도잉+이어묻기 |
| **M7** | 분기·요약 | edit-fork(분기 트리)+summarize-thread |
| **M8** | 스레드·설정 | ThreadSidebar+manage-thread+이력복원+SettingsPage+slash-command |
| **M9** | 폴리시 | 에러·재연결·내보내기(PDF/docx)·a11y·반응형 |

각 M은 backend 계약을 mock으로 먼저 통과 → 실제 연결.

---

## 11. Check 기준 (다음 PDCA 단계)
**기능**: 스트리밍 in-place→확정 / 도구 셀 매핑 / 리치 블록 정확 렌더 / 위젯 값변경 즉시 재계산 / 누적 근거 중복제거·점프 / 분기 보존 / 요약 후 컨텍스트 축소 / 승인 게이트.
**보안(Zero-Trust)**: 새니타이즈로 `<script>`·이벤트핸들러 제거 검증 / raw HTML 미실행 / 위젯 파라미터 검증·미지원 폴백 / 링크 확인.
**비기능**: O(n)(긴 스레드에서 토큰 1개 추가가 전체 재렌더 안 함, 프로파일) / SSE 재연결·중복방지 / a11y / **SoC 린트**(위젯·페이지에서 직접 fetch/EventSource 0).
**테스트**: Vitest(렌더 파이프라인·새니타이즈·위젯 계산·슬래시·store 전이) / Playwright(전송→스트리밍→도구→위젯→승인→분기 happy path, 재연결) / mock SSE 픽스처.

---

## 12. 리스크 & 완화
| 리스크 | 완화 |
|---|---|
| 모델 HTML/스크립트 주입 | DOMPurify allowlist + 샌드박스 + raw HTML 금지(D9) |
| 위젯 파라미터 악용/오류 | 스키마 검증 + 고정 카탈로그 + 폴백, 계산은 FE 소유 |
| 토큰 스트림 미지원 | message.completed로 시작, backend `messages` 모드 도입 시 delta |
| 부분 마크다운 깜빡임 | 블록 요소 지연 렌더(6.3) |
| 긴 스레드 성능 | append-only + 윈도잉 |
| 분기 UI 복잡도 | 분기 트리 최소 네비부터, 점진 고도화 |
| EventSource 인증 헤더 | fetch-event-source로 토큰 주입 |

---

## 13. Definition of Done
M0–M9 수용 기준 + 11장 Check(기능·보안·비기능·테스트) 통과. `shared/api`·`shared/render`·`shared/widgets` 외부에서 직접 네트워크/원시 렌더/임의 실행 0. Plan과 구조 일치, `.env.example` 제공.

## 14. 사전조건
backend 4장 계약(SSE+명령) 제공 또는 mock 합의. agent `stream`이 SSE로 표면화. Node 20+, pnpm 권장.

## 부록 — Do 진입 액션
1. D1–D10 사인오프.
2. M0 스캐폴딩 + FSD 의존 린트.
3. `shared/api/contracts.ts`(4장) + `shared/widgets/schema.ts`(7장)를 단일 소스로 먼저 고정.
