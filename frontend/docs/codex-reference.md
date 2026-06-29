# OpenAI Codex 구조 조사 → Svelte 프론트엔드 설계 참고

> 목적: 우리 Agent 프론트엔드(Svelte · FSD)를 설계하기 위해 OpenAI Codex의
> 코드 아키텍처와 UI/UX 구조를 조사하고, FSD 레이어로 매핑한다.
> 조사일: 2026-06-22

---

## 1. Codex 전체 그림

Codex는 하나의 앱이 아니라 **공통 엔진 + 여러 표면(surface)** 구조다.

```
                    ┌──────────────────────────────┐
   표면(UI)  TUI ── │   protocol (Submission/Event) │ ── core 엔진
            IDE ──  │      = 단일 통신 규약          │   (codex-core)
            exec ── └──────────────────────────────┘
            cloud
```

핵심 통찰: **UI는 엔진을 직접 호출하지 않는다.** 모든 표면(터미널 UI, IDE
확장, 헤드리스 실행, 클라우드)은 `protocol` 계층의 **이벤트 스트림 +
명령 제출(submission) 채널**만 통해 엔진과 대화한다. 표면을 추가해도
엔진은 그대로다. → 우리 Svelte FE도 이 "표면" 중 하나로 설계하면 된다.

---

## 2. 코드 아키텍처 (codex-rs)

Rust Cargo 워크스페이스, 100+ crate의 모노레포. 기술적 세부보다 **계층 분리
방식**이 참고 포인트다.

| 계층 | crate | 책임 |
|------|-------|------|
| 진입점 | `cli/` | 멀티툴 디스패처 (서브커맨드 라우팅) |
| 표면 | `tui/` | 풀스크린 터미널 UI (Ratatui) |
| 표면 | `exec/` | 헤드리스/비대화형 실행 |
| 표면 | `app-server/` | IDE 확장·데스크톱 앱 연동 서버 |
| **엔진** | `core/` | 세션·모델·도구 오케스트레이션 (비즈니스 로직) |
| 규약 | `protocol/` | 이벤트·에러·모델 등 공유 타입 |
| 도구 | `mcp-server/`, `skills/`, `hooks/` | MCP·스킬·라이프사이클 훅 |

`codex-core`가 노출하는 주요 타입:

- `CodexThread` — 단일 대화 스레드/턴 상태
- `ThreadManager` — 스레드 생성·포크·재개 관리
- `ModelClient` — LLM API 통신·재시도
- `RolloutRecorder` — 세션 영속화·이벤트 로깅
- `McpManager` — MCP 서버 연결 관리
- `SkillsManager` — 스킬 탐색·주입

### 우리 프로젝트에의 시사점 (SoC)
- **엔진(core) ↔ 표면(UI) ↔ 규약(protocol) 3분할**은 우리 구조와 정확히 일치한다:
  `agent/`(엔진) · `frontend/`(표면) · `backend/`(규약·API 게이트웨이).
- FE는 도메인 로직을 갖지 않고, backend의 이벤트/명령 규약만 소비한다.

---

## 3. UI/UX 구조 (TUI 기준)

Codex 터미널 UI는 **계층형 위젯 + 이벤트 버스** 구조다. 화면은 크게
세 영역으로 나뉜다.

```
┌─────────────────────────────────────────┐
│                                         │
│   ChatWidget (대화 transcript)           │  ← 위: 대화 표시 영역
│   - 확정 셀 + 스트리밍 중인 active_cell    │
│                                         │
├─────────────────────────────────────────┤
│   BottomPane                            │  ← 아래: 입력 + 오버레이
│   - ChatComposer (텍스트 입력)            │
│   - Overlay 스택 (승인·선택·MCP 폼)        │
├─────────────────────────────────────────┤
│   StatusLine / Footer (스피너·단축키 힌트) │  ← 상태 표시
└─────────────────────────────────────────┘
```

### 주요 컴포넌트와 역할
| 컴포넌트 | 역할 |
|----------|------|
| `App` | 최상위 코디네이터. 렌더링·입력 라우팅·앱 이벤트 버스 |
| `ChatWidget` | 대화 뷰 + 상태 머신. protocol 이벤트 → UI 셀로 변환 |
| `HistoryCell` (trait) | 모든 transcript 항목의 렌더 인터페이스 |
| ├ `UserHistoryCell` | 사용자 메시지(첨부·멘션 포함) |
| ├ `AgentMessageCell` | 어시스턴트 응답(마크다운) |
| └ `PlainHistoryCell` | 단순 텍스트 항목 |
| `BottomPane` | 입력 푸터. ChatComposer + 팝업/모달 스택 |
| `ChatComposer` | 편집 가능한 프롬프트 입력 |
| `StatusIndicatorWidget` | "작업 중" 스피너·인터럽트 힌트 |
| Overlays/Popups | 승인 오버레이, 선택 팝업, MCP elicitation 폼 |

### 이벤트 흐름 (2-경로 + 버스)
1. **입력 경로**: 키 입력 → `TuiEvent` → `App` → 위젯 트리(주로 BottomPane/Composer)
2. **프로토콜 경로**: 엔진 이벤트 → `AppServerSession` → `App` → `ChatWidget`
   → active/committed 셀로 버퍼링 (스트리밍)
3. **AppEvent 버스**: 팝업 열기·설정 저장·종료 등 컴포넌트 간 내부 조정

### UX 패턴 (차용할 것)
- **슬래시 커맨드**: `/` 입력 시 빠른 동작(`/model`, `/review`, `/mcp`).
  작업 중에는 일부 커맨드 비활성화.
- **스트리밍 셀**: 응답을 한 번에 그리지 않고 active_cell을 in-place 갱신.
- **승인 워크플로**: 위험 동작 전 오버레이로 사용자 승인 요청 (Zero-Trust UX).
- **transcript 오버레이**: 전체 대화 로그를 토글(Ctrl+T)로 펼침.

---

## 4. FSD Svelte 매핑 (제안)

Codex 구조를 우리 `frontend/src/` FSD 레이어로 옮기면:

| FSD 레이어 | Codex 대응 | 우리 구현(예시) |
|------------|-----------|----------------|
| `app/` | `App` 코디네이터, 이벤트 버스, 전역 설정 | 라우팅, 프로토콜 클라이언트 프로바이더, 테마 |
| `pages/` | 전체 화면 조합(TUI 단일 화면) | `ChatPage`(대화), `SettingsPage` |
| `widgets/` | `ChatWidget`, `BottomPane`, `StatusLine` | `ConversationView`, `Composer`, `StatusBar` |
| `features/` | 슬래시 커맨드, 승인, 모델 선택, 스트리밍 | `send-message`, `approve-action`, `slash-command`, `select-model` |
| `entities/` | `HistoryCell`(User/Agent/Plain), 스레드, 세션 | `message`, `thread`, `session` 엔티티 + 셀 컴포넌트 |
| `shared/` | protocol 타입, 이벤트 채널, UI 프리미티브 | API 클라이언트(SSE/WS), 공통 UI, 타입 |

### 권장 설계 원칙 (우리 프로젝트 규칙 반영)
- **SoC**: FE는 표시·입력만. 추론·DB 접근은 backend/agent 규약으로 위임.
- **이벤트 구동**: backend ↔ FE를 SSE/WebSocket **이벤트 스트림 + 명령 제출**
  단일 규약으로 (Codex protocol 계층 모방). `shared/api`에 격리.
- **스트리밍 우선**: 메시지 엔티티는 "확정/스트리밍" 두 상태를 갖도록 설계.
- **Zero-Trust UX**: 도구 실행·DB 쓰기 등 위험 동작은 승인 feature로 게이트.
- **O(n) 렌더**: 대화 셀은 append-only 리스트로, 매 틱 전체 재구성 대신 tail만 갱신.

---

## 4.5 Codex **데스크탑 앱** 벤치마크 (2026)

> TUI(§3)는 단일 화면이었지만, 데스크탑 앱은 **여러 에이전트 스레드를 병렬로 굴리는
> 커맨드 센터**다. 우리 멀티 에이전트 플랫폼과 골격이 같아 패턴을 적극 차용한다.
> 단, 코딩 전용 기능(diff·PR·터미널·SSH·worktree·in-app browser)은 제외하고
> 업무 에이전트에 가치 있는 패턴만 흡수한다.

### 데스크탑 앱의 핵심 표면
- **프로젝트 단위 스레드 조직**: 스레드를 프로젝트로 묶고 상태로 필터. 컨텍스트 손실 없이 전환.
- **Task 사이드바**: 실행 중 에이전트의 **계획·출처·생성 산출물**을 실시간 노출.
- **Artifact viewer**: PDF·스프레드시트·문서·슬라이드 등 **비코드 산출물을 창 안에서 미리보기**.
- **Review queue**: 자동 에이전트가 무단 반영하지 못하게 막는 **HITL 통제면**. 변경을 검토→승인/수정.
- **Local / Cloud 모드 + 비동기 재개**: 원격 실행 시 창을 닫았다 와도 이어받음.
- **Automations / Triggers**: 스케줄·이벤트로 스레드 자동 시작.

### 우리 적용 (채택 / 적응 / 제외)

| Codex 데스크탑 패턴 | 우리 적용 | FSD 위치 |
|---|---|---|
| Task 사이드바(계획·출처·산출물 실시간) | **채택** — 우측 패널 탭 `계획/출처/산출물` | `widgets/TaskSidebar` |
| Artifact viewer | **채택** — 산출물 미리보기(법령 별표 PDF·표·보고서) | `widgets/ArtifactViewer`, `entities/artifact` |
| Review queue(HITL) | **채택·승격** — Zero-Trust 승인을 *큐 surface*로 | `features/review-queue` |
| 프로젝트 단위 스레드 | **채택** — 프로젝트 × 에이전트 × 스레드 | `entities/project`, `widgets/ThreadSidebar` |
| Local/Cloud + 닫고 재개 | **적응** — 비동기 런 + SSE 재연결(닫고 와도 이어짐) | `shared/api`(디커플드 브로커) |
| Automations/Triggers | **후순위** — 스케줄 런 | (2차) |
| diff·PR·터미널·SSH·worktree·in-app browser | **제외** — 코딩 전용 | — |

### 멀티 에이전트로의 번안 (Codex에 없던 우리 고유)
- **에이전트 레일**: 좌측에 분야 에이전트 함대(자동 라우팅 + 법률·세무·건설…).
- **라우팅 투명성**: supervisor 자동 라우팅의 "어느 에이전트로·왜"를 *라우팅 셀*로 노출.
- **핸드오프 표시**: 에이전트 간 제어 위임을 트랜스크립트에 셀로.

> 시각 언어: Codex의 "다중 패널 커맨드 센터 + operator 밀도"를 가져오되,
> 우리 **신뢰·중립 톤**(법률 색 배제, 도메인은 활성 에이전트가 칠함)으로 흡수.

---

## 5. 다음 단계 후보
1. `shared/api` — backend 이벤트 스트림/명령 규약 정의 (SSE), `routing.decided`·`handoff`·`artifact` 이벤트 포함
2. `entities/message` + `entities/artifact` + `entities/project` — 모델·셀 골격
3. `widgets/ConversationView` · `Composer` · `TaskSidebar`(계획/출처/산출물) — 핵심 화면
4. `features/review-queue` — HITL 승인 큐 surface
5. `app/` — 라우팅·프로바이더·이벤트 버스 셋업 (SvelteKit static 확정)

---

## 출처
- [codex-rs/README.md (Code Organization)](https://github.com/openai/codex/blob/main/codex-rs/README.md)
- [Repository Structure | DeepWiki](https://deepwiki.com/openai/codex/1.2-repository-structure)
- [Terminal User Interface (TUI) | DeepWiki](https://deepwiki.com/openai/codex/4.1-terminal-user-interface-(tui))
- [CLI – Codex | OpenAI Developers](https://developers.openai.com/codex/cli)
- [App – Codex | OpenAI Developers](https://developers.openai.com/codex/app) · [Features](https://developers.openai.com/codex/app/features)
- [Introducing the Codex app | OpenAI](https://openai.com/index/introducing-the-codex-app/)
- [Inside the Codex App Workspace: PR Review Pane, Task Sidebar, Artifact Viewer](https://codex.danielvaughan.com/2026/04/17/codex-app-workspace-pr-review-task-sidebar-artifact-viewer/)
- [Codex Desktop App: Automations, Triggers and the Review Queue](https://codex.danielvaughan.com/2026/04/08/codex-desktop-automations/)
