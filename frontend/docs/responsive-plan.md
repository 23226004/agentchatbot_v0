# PDCA Plan — 반응형 화면(Responsive) · v2

> **Feature**: `frontend/responsive` · **Phase**: `plan` · **작성**: 2026-06-30 (v1) · **개정**: 2026-06-30 (v2, 3관점 적대 비평 반영)
> **스택**: Svelte 5 runes · Vanilla CSS + 토큰 · 무의존(런타임). 검증용 devDep(Playwright)은 허용.
> **목표**: 데스크톱/태블릿/모바일 어디서나 **모든 기능 접근 + 터치 동작 + 본문(표·수식·근거) 정상 흐름**.

> **v2 개정 요지(교차검증으로 적발)**: v1은 "패널 셸(여닫기)"은 잘 풀었으나 **(1) 본문 콘텐츠 모바일 흐름(표·수식·긴URL 가로 오버플로)·(2) 접근성 모달 의미·(3) 모바일 키보드/뷰포트(dvh)·(4) ApprovalBar 배치·(5) 인용 칩이 링크조차 아님** 을 통째로 누락. 또 **기존 버그 2건**(요약 패널 무스타일·`viewport-fit` 누락→safe-area 무력) 발견. 아래 전면 반영.

---

## 1. 현황 + 문제(P1~P16)

레이아웃: `.body` = CSS grid `52px(AgentRail) 190px(Threads) 1fr(center) 300px(Task)`. 기존 `@media 1040`(Task 숨김)·`760`(Task+Threads 숨김). `.shell{height:100vh}`.

| # | 문제 | 심각도 | 코드근거 |
|---|------|--------|----------|
| P1 | 모바일서 대화목록 숨김 → 새대화·전환·이름변경·삭제 불가 | 🔴 | media 760 `display:none` |
| P2 | 모바일서 계획/근거 숨김 | 🟠 | 동일 |
| P3 | 숨긴 패널 다시 여는 토글 없음 | 🔴 | — |
| P4 | 헤더 항목 과다(brand·active·status·ran·Model·검토칩·요약·테마) 오버플로 | 🟡 | header |
| P5 | 호버 액션(rename/delete·fork ⑂) 터치서 트리거 불가 | 🟠 | ThreadSidebar `.actions opacity:0`·ConversationView `.fork .row:hover` |
| P6 | AgentRail 항상 표시(1종) | ⚪ | — |
| P7 | safe-area 미고려 | 🟡 | — |
| P8 | 터치 타깃·폰트 데스크톱 기준 | 🟡 | Composer 입력 13px |
| **P9** | ★**본문 가로 오버플로** — 표·KaTeX 수식·긴URL/토큰에 스크롤 래퍼·`overflow-wrap` **전무**(`pre`만 있음). "가로 스크롤 0" 기준과 정면 모순 | 🔴 | `Markdown.svelte:49,52` `table`엔 overflow 없음·`overflow-wrap` 0건 |
| **P10** | ★**`100vh` 모바일 버그** — iOS 주소창/키보드서 Composer 가림 | 🔴 | `ChatPage.svelte:420` |
| **P11** | 가상 키보드(visualViewport) 미대응 | 🟠 | — |
| **P12** | ★**기존 버그: `.summary-panel` 무스타일** — CSS 규칙 0건(요약 패널 깨짐, 데스크톱서도) | 🔴 | grep `.summary-panel{`=0 |
| **P13** | 기존 버그: `viewport-fit=cover` 누락 → `env(safe-area-*)`가 항상 0(P7 보완 무력화) | 🟠 | `app.html:6` |
| **P14** | 인용 칩이 **링크/버튼이 아님**(`<sup> cursor:default`) → 칩→근거 점프 불가(데스크톱 포함), 모바일선 hover 툴팁마저 없어 무의미 번호 | 🔴 | `Markdown.svelte:55-58` |
| **P15** | 오버레이 a11y(포커스 트랩·`aria-modal`·`inert`·Esc·복귀포커스·가시 닫기) 부재 | 🔴 | — |
| **P16** | 방향전환/리사이즈 시 `navOpen/taskOpen` 잔류·scroll lock·z-index·SPA 뒤로가기 닫기 미정의 | 🟠 | — |

---

## 2. 브레이크포인트 + 콘텐츠 흐름

| Tier | 폭 | 레이아웃 |
|------|-----|---------|
| **Wide** | `≥ 1440px` | 데스크톱 + **center `max-width: ~760px; margin-inline:auto`**(가독 폭, P 신규) |
| **Desktop** | `1040–1439` | 현행 다컬럼 |
| **Tablet** | `760–1039` | 2컬럼(Threads+center) + Task 토글 드로어 |
| **Mobile** | `< 760` | 1컬럼(center) + Threads 좌측 드로어 + Task/근거(R3 참조) + AgentRail 숨김 |
| **검증 추가폭** | `320`(소형폰)·`1920`(광폭)·**landscape**(`max-height:480`) | 별도 |

높이: `.shell { height:100dvh }`(폴백 `100vh; 100dvh;`). `app.html` meta에 **`viewport-fit=cover`** 추가(safe-area 활성 전제).

---

## 3. 확정 결정 (Do 진입 전 사인오프) — v2 개정

| # | 결정 | 권고 | 근거/개정 |
|---|------|------|----------|
| **R1** | 브레이크포인트 | Wide/Desktop/Tablet/Mobile(+320·1920·landscape 검증) | 광폭 가독·landscape 누락 보완 |
| **R2** | 모바일 대화목록 | **좌측 오프캔버스 드로어 + 햄버거** | 표준. 단 R7·R8·R16 동반 |
| **R3** | **근거(citation) 접근** | ★**인용 칩을 `<button>`으로 승격 + 탭 시 그 문단 아래 근거 카드 인라인 확장**(본문 가림 0). 목록은 Task 패널/시트는 보조. *전면 하단 시트 폐기* | v1의 "전면 시트"는 답변↔근거 동시대조(법령앱 본질) 파괴. 칩이 현재 링크도 아님(P14) |
| **R4** | 헤더 응축 | 요약·테마·(검토)는 **더보기(⋮)**, **모델은 헤더에 축약 칩 상시**(탭→시트). **mismatch 경고 절대 숨김 금지**. status는 **토스트/스낵바**(숨김 아님 — 에러 전달) | 모델=매턴 결정·신뢰신호. status는 에러도 실음 |
| **R5** | AgentRail | Tablet 이하 숨김(드로어 상단 통합) | 1종 |
| **R6** | 구현 | CSS 미디어쿼리 + runes 상태. 런타임 무의존. **검증 devDep Playwright 허용** | — |
| **R7** | 회귀 목표(★재서술) | **데스크톱 *렌더/동작* 회귀 0, 마크업은 *추가만*(이동·삭제 없음)**. 드로어/백드롭/wrapper는 **`.body` grid 밖**에 배치(track 오염 방지). "마크업 불변"은 폐기(오프캔버스=새 DOM 불가피) | grid child를 fixed로 빼면 백드롭·inert·닫기 DOM 필요 |
| **R8(신규)** | 접근성 | 모든 오버레이 = `aria-modal`+focus trap+`inert`(닫힌측)+Esc+복귀포커스+**가시 닫기 버튼**. 케밥 `role=menu`(화살표·Esc) | v1 침묵 |
| **R9(신규)** | 뷰포트/키보드 | `100dvh` + `viewport-fit=cover` + 입력 **16px**(iOS 줌 방지) + (후속) visualViewport 보정 | P10/P11/P13 |
| **R10(신규)** | ApprovalBar 모바일 | 승인 요청 시 **모달 바텀시트로 격상** + 키보드 dismiss + 체크박스/버튼 ≥44px | P5/하단 3중충돌 |
| **R11(신규)** | 본문 오버플로 정책 | 표·`.katex-display`·`pre`는 **래퍼 `overflow-x:auto`(가로 스크롤 허용)**, 본문·버블 `overflow-wrap:anywhere`. "가로 스크롤 0"의 **명문 예외**(이 래퍼 내부만 허용) | P9 |
| **R12(신규)** | 터치 액션 | **케밥(⋮) 상시 노출**(데스크톱은 저대비, 호버 강조 보조). `hover:none` 단독 의존 금지(하이브리드 오작동) | P5 |
| **R13(신규)** | 오버레이 인프라 | scroll lock(컨테이너 `overflow:hidden`+위치보존)·z-index 레이어·**SPA 뒤로가기로 드로어 닫기**(`pushState`/`popstate`) | P16 |
| **R14(신규)** | 상태 리셋 | 뷰포트가 데스크톱 폭이 되면 `navOpen/taskOpen` 강제 false. 자동닫힘은 **콜백 wrapper**에서(early-return 무관) | P16/busy early-return 충돌 |
| **R15(신규)** | 최소지원/브라우저 | 최소 320px·iOS Safari/Chrome Android 최신 2버전. 다크×모바일 검증 포함 | — |

---

## 4. 구현 단계(Do) + **MVP 경계**

> **순서 원칙**: 기존 버그·뷰포트 토대 먼저 → 본문 흐름(가장 잦은 깨짐) → 셸(드로어/시트) → 응축/접근성. P1(치명)의 **액션(rename/delete)은 드로어와 같은 단계**에서 터치 가능해야(v1 M2↔M5 순서 모순 해소).

### 🚩 MVP(출시 차단 — 모바일 사용 불가/깨짐 유발)
- **M0 — 기존 버그·토대**: `.summary-panel` CSS 신설(P12)·`100dvh`+`viewport-fit=cover`(P10/P13)·center `max-width`(광폭)·입력 16px(P8). 데스크톱 회귀 0.
- **M1 — 본문 콘텐츠 적응(P9)**: 표/`.katex-display`/긴토큰 래퍼 `overflow-x:auto`+`overflow-wrap:anywhere`(R11). toolrow/요약/버블 좁은폭 점검.
- **M2 — 대화목록 드로어 + 터치 액션(P1/P3/P5 일부)**: ThreadSidebar 오프캔버스(R7 grid밖)+햄버거+백드롭+자동닫힘(R14)+스크롤락/뒤로가기(R13)+**rename/delete 케밥**(R12)+a11y(R8). *드로어와 액션을 한 단계로.*
- **M3 — 근거 접근(P2/P14)**: 인용 칩 `<button>` 승격 + 인라인 확장 근거 카드(R3). Task 패널 토글(태블릿 드로어/모바일).
- **M4 — ApprovalBar 모바일(P5/HITL)**: 모달 바텀시트 격상(R10).

### 🔵 후속(있으면 좋음)
- **M5 — 헤더 응축(P4)**: 더보기(⋮)·모델 칩 상시·status 토스트(R4).
- **M6 — fork 케밥·터치 정돈(P5)**: ⑂를 메시지 케밥으로.
- **M7 — 폴리시**: AgentRail(P6)·landscape 세부·visualViewport 키보드 보정(P11)·스와이프.

---

## 5. 검증 — 컴포넌트 × 뷰포트 매트릭스 + 자동화

**자동(Playwright 스모크, devDep)**: 뷰포트 {320·360·768·1280·1920} × {라이트·다크} 에서
- `scrollingElement.scrollWidth ≤ clientWidth + ε` (가로 오버플로 0 — R11 래퍼 내부 제외)
- 드로어 열림/닫힘 토글·백드롭 닫힘·뒤로가기 닫힘
- 케밥 메뉴 → rename/delete/fork 액션 **도달**(hover 없이)
- Composer 가시(하단 노출)
- 데스크톱(1280) **스크린샷 회귀**(R7 증명)

**수동(자동화 한계)**: iOS Safari 실기/시뮬 — 키보드 올라온 상태 Composer 가시(P10/P11)·safe-area(P13).

**기능×뷰포트 매트릭스(행)**: ThreadSidebar·TaskSidebar·헤더·Composer·**ApprovalBar**·요약패널·ModelSelector·CitationList·**Markdown(표/코드/수식/긴URL)**·toolrow·thinking·fork·**인용칩 확장**. 각 칸 = 도달/오버플로/터치트리거/a11y(focus·SR) 판정.

---

## 6. 미해결/후속
- visualViewport 정밀 키보드 보정·스와이프 제스처·orientation 세부.
- `follow()`/자동스크롤이 dvh/키보드 높이변동에 흔들리지 않게 안정화(M0/M2 회귀 점검 필수 — `scroller.clientHeight` 결합).
- 다종 에이전트 도입 시 AgentRail/드로어 재설계.
- 긴 대화 가상 스크롤(윈도잉) — 별도 성능 과제.

---

## 부록 — 발견된 기존 버그(반응형과 별개, 즉시 수정 권고)
1. **`.summary-panel` 무스타일**(P12) — 요약 클릭 시 깨짐. 데스크톱서도 영향.
2. **`viewport-fit=cover` 누락**(P13) — safe-area 무력.
3. **인용 칩 비-인터랙티브**(P14) — 데스크톱서도 칩→근거 점프 불가.
