# PDCA Plan — Frontend (AI Agent Service)

> **Feature**: `frontend` · **Phase**: `plan` · **작성일**: 2026-06-24
> **스택**: Svelte 5 (runes) · SvelteKit(static) · FSD · SSE+REST
> **근거 문서**: [`codex-reference.md`](./codex-reference.md), 루트/agent/backend README
> **원칙**: SoC · FSD · Zero-Trust · O(n) 렌더 · 최소 의존

---

## 0. 확정 필요 결정 (Do 진입 전 사인오프)

| # | 결정 | 채택(베이스라인) | 근거 | 대안 |
|---|------|-----------------|------|------|
| D1 | 빌드 기반 | **SvelteKit + adapter-static (SPA)** | 표준·라우팅 내장·SSR 전환 여지 | Vite+Svelte SPA |
| D2 | 스트리밍 규약 | **SSE(이벤트) + REST POST(명령)** | ReAct 단계 스트리밍 최적·인프라 단순 | WebSocket |
| D3 | 스타일링 | **Vanilla CSS + 디자인 토큰 + scoped style** | 무의존·FSD 캡슐화·토큰 중앙화 | Tailwind |
| D4 | 상태 | **Svelte 5 runes** (`$state`/`$derived`/`$effect`) + `.svelte.ts` 스토어 | 요구사항 | — |
| D5 | MVP 범위 | **Full** (아래 2장) | 사용자 선택 | Lean |

> 위 표가 이 Plan의 전제다. 뒤집으면 4·5·7장 일부가 바뀐다.

---

## 1. 목표 & 배경 (Why)

AI Agent 서비스의 **사용자 표면(surface)**을 만든다. Codex 아키텍처의 통찰대로
**FE는 엔진(agent)을 직접 호출하지 않고**, backend가 노출하는 **이벤트 스트림 +
명령 제출** 규약만 소비한다. 표면을 추가/교체해도 엔진은 불변이다.

- **무엇을**: 대화형 에이전트 UI (스트리밍 응답, 도구 호출 가시화, 다중 스레드).
- **왜**: agent(ReAct 엔진)는 이미 `run/stream`을 제공한다. 사용자가 이를 쓰려면
  스트리밍·승인·이력을 갖춘 표면이 필요하다.
- **무엇이 아닌가**: FE는 추론·DB 접근·도메인 로직을 갖지 않는다(전부 backend 위임).

---

## 2. 스코프 (Full MVP)

### In scope
1. **다중 대화 스레드** — 생성/전환/삭제, 사이드바 목록.
2. **스트리밍 대화** — 사용자 메시지 → 어시스턴트 응답을 토큰/단계 단위로 in-place 표시.
3. **도구 호출 시각화** — ReAct의 `tool_call → tool_result`를 접이식 셀로 노출.
4. **승인 워크플로(Zero-Trust UX)** — 위험 동작(도구 실행·DB 쓰기 등) 전 승인 모달.
5. **슬래시 커맨드** — `/model`, `/new`, `/clear` 등 빠른 동작.
6. **설정** — 모델 선택, 서버 URL, 테마(라이트/다크).
7. **이력/영속** — 스레드·메시지 backend 영속, 재방문 시 복원.

### Non-goals (이번 단계 제외)
- 자체 인증/회원 시스템 구축(토큰 주입 지점만 마련).
- 모바일 네이티브, 다국어(한국어 우선), 오프라인 모드.
- 음성/파일 첨부(엔티티 확장 여지만 남김).

---

## 3. 아키텍처

### 3.1 표면 모델 (Codex protocol 차용)

```
 사용자 ── 입력 ─▶ features(send/approve/slash) ─▶ shared/api(command: REST POST)
                                                          │
 backend(FastAPI) ◀───────────────────────────────────────┘
        │  SSE event stream
        ▼
 shared/api(event channel) ─▶ entities(message/thread 상태, runes) ─▶ widgets(렌더)
```

- **단일 규약 격리**: 모든 backend 통신은 `shared/api`에만 존재. 위젯/페이지는
  HTTP를 모른다 → 백엔드 교체가 `shared/api` 경계 안에서만 일어난다.
- **이벤트 구동**: 명령은 POST, 수신은 SSE. 두 경로를 `shared/api`가 캡슐화.

### 3.2 FSD 레이어 매핑 (의존 방향: 상위→하위만)

`app → pages → widgets → features → entities → shared`

| 레이어 | 책임 | 핵심 산출물 |
|--------|------|------------|
| `app/` | 전역 설정·프로바이더·테마·라우팅 진입 | 앱 셸, 테마 토큰 주입, api 클라이언트 프로바이더 |
| `pages/` | 라우트 단위 화면 조합 | `ChatPage`, `SettingsPage` |
| `widgets/` | 독립 UI 블록 | `ConversationView`, `Composer`, `ThreadSidebar`, `StatusBar` |
| `features/` | 사용자 행동 단위 | `send-message`, `approve-action`, `slash-command`, `select-model`, `manage-thread` |
| `entities/` | 비즈니스 엔티티 + 셀 | `message`(User/Agent/Tool 셀), `thread`, `session` |
| `shared/` | 공통 규약·UI·유틸 | `api`(SSE/REST), `ui`(토큰·프리미티브), `lib`(타입·유틸) |

> SvelteKit `src/routes/`는 page를 조합만 하는 **얇은 진입점**. 도메인은 `src/lib/` FSD에.

---

## 4. 백엔드 계약 (protocol) — Plan 초안

> backend 팀과 합의해야 하는 인터페이스. FE는 이 스키마에 맞춰 `shared/api`를 짠다.

### 4.1 명령 (REST POST)
| 메서드·경로 | 용도 | 비고 |
|---|---|---|
| `POST /threads` | 스레드 생성 → `{thread_id}` | |
| `GET /threads` | 스레드 목록 | |
| `GET /threads/{id}/messages` | 이력 복원 | |
| `POST /threads/{id}/messages` | 메시지 전송 → `{run_id}` | 실행 시작 |
| `GET /threads/{id}/stream?run_id=` | **SSE** 이벤트 수신 | 또는 POST가 직접 SSE 반환 |
| `POST /runs/{id}/interrupt` | 실행 중단 | |
| `POST /runs/{id}/approve` | 승인 결정 `{decision}` | Zero-Trust 게이트 |
| `GET /models`, `GET/PUT /settings` | 설정 | |

### 4.2 이벤트 (SSE `event:` 타입) — agent의 `stream(updates)`와 정합
| event | payload(예) | UI 반응 |
|---|---|---|
| `run.started` | `{run_id, thread_id}` | 스피너 on |
| `message.delta` | `{text}` | active 셀 in-place 추가(O(n) tail) |
| `tool.call` | `{id, name, args}` | 도구 호출 셀(접힘) |
| `tool.result` | `{id, content}` | 해당 셀에 결과 채움 |
| `message.completed` | `{text}` | 셀 확정(committed) |
| `approval.requested` | `{id, action, detail}` | 승인 모달 → `/approve` |
| `error` | `{message}` | 에러 셀 |
| `run.done` | `{run_id}` | 스피너 off |

> agent가 토큰 단위 스트림을 안 주면(현재 `stream_mode="updates"`는 단계 단위),
> `message.delta` 대신 `message.completed`만 쓰는 모드로 시작하고, backend에서
> `stream_mode="messages"`(토큰) 도입 시 delta를 활성화한다. — **Check 단계 확인 항목**.

---

## 5. Svelte 5 Runes 적용 지침

- **로컬 상태**: `let x = $state(...)`. 파생값은 `$derived`/`$derived.by`.
- **부수효과/구독**: `$effect(() => { const off = subscribe(); return off; })` — SSE 구독·정리.
- **props**: `let { thread } = $props()`; 양방향은 `$bindable`.
- **공유 스토어**: `.svelte.ts` 모듈에서 `$state` 객체/클래스를 export (스토어 대체).
  예) `entities/thread/thread-store.svelte.ts` → `class ThreadStore { threads = $state([]) ... }`.
- **O(n) 렌더 규칙**: transcript는 **append-only** 배열. 매 이벤트마다 전체 재구성 금지,
  마지막(active) 셀만 갱신. `{#each ... (id)}` 키로 안정 재사용.
- **렌더 조합**: 셀 타입 분기는 `{#if}` + 컴포넌트 매핑 또는 snippet(`{#snippet}`/`{@render}`).

---

## 6. FSD 레이어별 산출물 (Do 단계 파일 목록)

```
src/lib/
  shared/
    api/
      client.ts          # REST(fetch) 래퍼 + 에러 정규화
      events.ts          # SSE 구독(EventSource/fetch-event-source), 이벤트 타입
      contracts.ts       # 4장 스키마의 TS 타입(단일 소스)
    ui/
      tokens.css         # 디자인 토큰(색·간격·타이포·밀도, 라이트/다크)
      Button.svelte / Modal.svelte / Spinner.svelte / Markdown.svelte
    lib/ (utils, id, time)
  entities/
    message/
      types.ts           # Message(role, status: 'streaming'|'committed', toolCalls)
      message-store.svelte.ts
      UserCell.svelte / AgentCell.svelte / ToolCell.svelte
    thread/
      thread-store.svelte.ts   # 목록·활성 스레드(runes)
      types.ts
    session/  (설정·연결 상태)
  features/
    send-message/        # composer 제출 → POST + SSE 연결
    approve-action/      # 승인 모달 + /approve
    slash-command/       # '/' 파서·디스패치
    select-model/        # 모델 선택
    manage-thread/       # 생성·전환·삭제
  widgets/
    ConversationView.svelte   # transcript(append-only) 렌더
    Composer.svelte           # 입력 + 슬래시 힌트
    ThreadSidebar.svelte
    StatusBar.svelte
  pages/
    ChatPage.svelte
    SettingsPage.svelte
  app/
    App.svelte / theme.ts / providers.ts
src/routes/               # SvelteKit 얇은 진입점 (+layout, +page → pages/* 조합)
```

---

## 7. 작업 분해 (마일스톤)

| M | 목표 | 산출물 | 수용 기준(Acceptance) |
|---|------|--------|----------------------|
| **M0** | 스캐폴딩 | SvelteKit+static, FSD 폴더, 토큰, lint/format | `dev` 기동, 빈 ChatPage 렌더, FSD 의존 규칙 린트 |
| **M1** | 규약 계층 | `shared/api`(contracts/client/events) + entities 모델 | mock 서버로 SSE 1건 수신→message 셀 표시 |
| **M2** | 핵심 대화 | Composer + ConversationView + send-message | 메시지 전송→스트리밍 응답 in-place, O(n) tail 갱신 |
| **M3** | 도구·승인 | ToolCell + approve-action | tool.call/result 셀 표시, approval 모달→/approve 동작 |
| **M4** | 다중 스레드 | ThreadSidebar + manage-thread + 이력복원 | 스레드 생성/전환/삭제, 재방문 복원 |
| **M5** | 커맨드·설정 | slash-command + SettingsPage + select-model | `/model`·`/new`·`/clear` 동작, 모델/테마 변경 |
| **M6** | 폴리시 | 에러·재연결·a11y·반응형·로딩 | 끊김 자동복구, 키보드 접근, 모바일 레이아웃 |

각 M은 backend 계약(4장)을 mock으로 먼저 통과 → 실제 backend 연결 순으로.

---

## 8. Check 기준 (다음 PDCA 단계 검증 항목)

**기능**
- 스트리밍 응답이 in-place로 갱신되고, 완료 시 committed로 확정되는가.
- tool.call/result가 셀로 정확히 매핑되는가(id 매칭).
- 승인 모달 없이는 위험 동작이 진행되지 않는가(Zero-Trust 게이트).
- 스레드 전환/삭제/복원이 상태 누수 없이 동작하는가.

**비기능**
- **O(n)**: N개 메시지에서 새 토큰 1개 추가가 전체 재렌더를 유발하지 않는가(프로파일).
- **재연결**: SSE 끊김 시 자동 재연결 + 중복 이벤트 방지(run_id/seq).
- **접근성**: 키보드 전용 조작, 포커스 관리, 명도 대비.
- **SoC**: 위젯/페이지에 `fetch`/`EventSource` 직접 호출 0건(린트 규칙).

**테스트**
- 단위: Vitest (api 파서, 슬래시 파서, store 전이).
- E2E: Playwright (전송→스트리밍→도구→승인 happy path, 재연결).
- mock SSE 픽스처로 backend 독립 검증.

---

## 9. 리스크 & 완화

| 리스크 | 영향 | 완화 |
|---|---|---|
| 토큰 단위 스트림 미지원(agent가 단계 단위) | 타이핑 효과 약함 | message.completed로 시작, backend `messages` 모드 도입 시 delta 활성 |
| EventSource 인증 헤더 불가 | 보호 API 호출 실패 | fetch-event-source로 Authorization 주입(Zero-Trust) |
| SvelteKit routes ↔ FSD 중복 | 구조 혼선 | routes는 얇은 진입점, 도메인은 `src/lib/` FSD 강제(린트) |
| 승인 워크플로 backend 미구현 | M3 지연 | 계약(4.1 /approve) 먼저 합의, mock으로 FE 선개발 |
| 다중 스레드 상태 누수 | 버그 | thread별 store 격리, 전환 시 SSE 구독 정리($effect cleanup) |

---

## 10. Definition of Done (이 Feature)

- 7장 M0–M6 수용 기준 전부 통과.
- 8장 Check 항목(기능·비기능·테스트) 통과, E2E happy path 녹색.
- `shared/api` 외부에서 직접 네트워크 호출 0(SoC 린트 통과).
- README + 본 Plan과 실제 구조 일치, `.env.example`(API base URL) 제공.

---

## 11. 사전조건 / 의존성

- backend가 4장 계약(SSE 이벤트 + 명령 엔드포인트)을 제공(또는 mock 합의).
- agent의 `stream`이 backend를 통해 SSE로 표면화됨(현 `stream_mode="updates"` → 이벤트 매핑).
- Node 20+, 패키지 매니저 합의(pnpm 권장).

---

## 부록 — 다음 액션 (Do 진입 시)
1. D1–D3 사인오프 확정.
2. M0 스캐폴딩 + FSD 의존 린트 규칙.
3. `shared/api/contracts.ts`를 4장 스키마로 먼저 고정(FE/BE 단일 소스).
