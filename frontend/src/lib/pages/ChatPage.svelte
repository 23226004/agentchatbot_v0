<script lang="ts">
  import { tick, onMount } from 'svelte';
  import { pushState } from '$app/navigation';
  import { page } from '$app/state';
  import AgentRail from '$lib/widgets/AgentRail.svelte';
  import ThreadSidebar from '$lib/widgets/ThreadSidebar.svelte';
  import ConversationView from '$lib/widgets/ConversationView.svelte';
  import Composer from '$lib/widgets/Composer.svelte';
  import TaskSidebar from '$lib/widgets/TaskSidebar.svelte';
  import ApprovalBar from '$lib/widgets/ApprovalBar.svelte';
  import ModelSelector from '$lib/widgets/ModelSelector.svelte';
  import { Transcript } from '$lib/entities/message/message-store.svelte';
  import { CitationLibrary } from '$lib/entities/citation/library.svelte';
  import { PlanStore } from '$lib/entities/plan/plan.svelte';
  import { AgentStore } from '$lib/entities/agent/profiles.svelte';
  import { ThreadStore } from '$lib/entities/thread/thread-store.svelte';
  import { ModelStore } from '$lib/entities/model/model-store.svelte';
  import { API_BASE } from '$lib/shared/api/config';
  import { fetchAgents } from '$lib/shared/api/agents';
  import {
    listThreads,
    createThread,
    getMessages,
    getCitations,
    renameThread,
    deleteThread,
    forkThread,
    summarizeThread,
    getSummaries,
    type Summary
  } from '$lib/shared/api/history';
  import { getModels, getSettingsModel, setSettingsModel } from '$lib/shared/api/models';
  import { createMockClient } from '$lib/shared/api/mock';
  import { createSseClient } from '$lib/shared/api/sse';
  import type { AgentEvent } from '$lib/shared/api/contracts';

  const transcript = new Transcript();
  const citations = new CitationLibrary();
  const plan = new PlanStore();
  const agents = new AgentStore();
  const threads = new ThreadStore();
  const models = new ModelStore();
  const client = API_BASE ? createSseClient(API_BASE) : createMockClient();

  // mock 데모 프리셋(로컬/GPT). 실 backend 면 onMount 의 GET /models 가 실제 레지스트리로 채운다.
  // (API_BASE 일 때 프리셋을 seed 하지 않음 — stale id 로 per-run 422 나는 것 방지.)
  if (!API_BASE) {
    models.load(
      [
        { id: 'qwen3.6-35b-a3b', provider: 'compatible' },
        { id: 'gpt-5.4-nano-2026-03-17', provider: 'openai' }
      ],
      'gpt-5.4-nano-2026-03-17'
    );
  }

  async function selectModel(id: string) {
    models.setActive(id);
    if (API_BASE) {
      try {
        await setSettingsModel(API_BASE, id); // 저장(런타임 적용은 backend 후속)
      } catch {
        /* 무시 */
      }
    }
  }

  let busy = $state(false);
  let answered = $state(false); // 답변/에러 도착 후 인디케이터 즉시 숨김(겹침 방지, 교차검증 B-F1)
  let interrupting = $state(false); // 중지 진행 중 — 협조취소 종결(run.done/error)을 '실패'로 표시 안 함
  let summaries = $state<Summary[]>([]); // 요약 목록
  let showSummary = $state(false); // 요약 패널 표시
  let summaryBusy = $state(false); // 요약 생성 중
  let status = $state('');
  let runModel = $state(''); // 직전 run 에서 backend 가 실제 사용한 모델(run.started.model) — 선택값 검증
  // HITL 승인 대기 — tools=대기 도구 목록(per-tool 선택 승인).
  let awaiting = $state<{
    detail: string;
    tools: Array<{ id: string; name: string; args?: Record<string, unknown> }>;
  } | null>(null);
  let scroller = $state<HTMLElement | null>(null);
  let theme = $state<'light' | 'dark'>('light');
  // 반응형 M2: 모바일 대화목록 드로어.
  // 열림 상태의 단일 진실원천 = history(SvelteKit shallow routing). raw history.pushState 는 SvelteKit
  // 라우터의 내부 인덱스를 덮어써 뒤로가기 시 전체 리로드를 유발 → $app/navigation 의 pushState 사용(교차검증 B).
  let navOpen = $derived(!!page.state.navDrawer); // 드로어 열림 = history 에 navDrawer 상태가 실려 있음
  let isNarrow = $state(false); // ≤760(드로어 모드) — inert/aria/포커스 토글 기준
  let hamburgerEl = $state<HTMLButtonElement | null>(null); // 닫힘 시 포커스 복귀 대상(R8)
  function openNav(): void {
    if (awaiting || taskOpen) return; // 승인 대기/우측 패널 열림 시 안 엶(상호배제 명시 — inert 타이밍 비의존, M3-A D2)
    pushState('', { navDrawer: true }); // history 항목 추가 → 뒤로가기 1번이 닫음(R13)
  }
  function closeNav(): void {
    if (navOpen) history.back(); // shallow-routed 항목 pop → page.state 복원 → navOpen=false (뒤로가기와 동일 경로)
  }

  // 반응형 M3: 우측 계획·근거 패널(TaskSidebar)을 ≤1040(태블릿+모바일)서 오프캔버스 드로어로(좌측 navDrawer 미러링).
  let taskOpen = $derived(!!page.state.taskDrawer);
  let isTabletDown = $state(false); // ≤1040: Task 패널이 드로어 모드(inert/aria/포커스 기준)
  let taskBtnEl = $state<HTMLButtonElement | null>(null); // 닫힘 시 포커스 복귀 대상
  function openTask(): void {
    if (awaiting || navOpen) return; // 승인 대기/좌측 드로어 열림 시 안 엶(상호배제 명시, M3-A D2)
    pushState('', { taskDrawer: true });
  }
  function closeTask(): void {
    if (taskOpen) history.back();
  }

  // ≤1040 진입/이탈 추적 + 와이드 복귀 시 task 드로어 강제 닫기(R14, navDrawer 와 동일 원리).
  $effect(() => {
    const mq = window.matchMedia('(max-width: 1040px)');
    isTabletDown = mq.matches;
    const onChange = () => {
      isTabletDown = mq.matches;
      if (!mq.matches && taskOpen) closeTask();
    };
    mq.addEventListener('change', onChange);
    return () => mq.removeEventListener('change', onChange);
  });

  // task 드로어 열림 동안: Esc 닫기 + 포커스 진입/복귀(R8). 하드웨어 뒤로가기는 SvelteKit 라우터가 처리.
  $effect(() => {
    if (!taskOpen) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        e.preventDefault();
        closeTask();
      }
    };
    window.addEventListener('keydown', onKey);
    void tick().then(() =>
      document
        .getElementById('task-drawer')
        ?.querySelector<HTMLElement>(
          'button, [href], input, textarea, select, [tabindex]:not([tabindex="-1"])'
        )
        ?.focus()
    );
    return () => {
      window.removeEventListener('keydown', onKey);
      if (taskBtnEl?.offsetParent) taskBtnEl.focus();
    };
  });

  // 반응형 M7/P11: 소프트 키보드 보정은 **선언적**으로만(app.html `interactive-widget=resizes-content` + 100dvh).
  // JS visualViewport(--vvh) 핸들러는 교차검증서 HIGH 결함 확인 → 제거: (D3) `.shell` 이 normal-flow 라 iOS 가
  // 하단 입력 포커스 시 layout viewport 를 스크롤(offsetTop>0)해 vv.height 만으론 여전히 키보드 뒤로 숨음 →
  // 올바른 수정은 position:fixed shell 재설계 + 실기 iOS 반복인데 blind 불가. (D5) 키보드 열림 시 autoscroll
  // 재-핀 부재. (D4) 데스크톱 핀치줌 오탐. → **정밀 iOS 보정은 백로그**(실기 테스트 필요). Android/Chrome 은
  // interactive-widget 로 layout viewport 가 축소돼 100dvh 가 따라가므로 선언적 처리로 충분.

  // 반응형 M5: 모바일 헤더 응축 — 요약·테마·검토는 ⋮ 더보기 메뉴로(모델 칩·mismatch 경고는 상시, R4).
  let moreOpen = $state(false);
  let moreEl = $state<HTMLElement | null>(null);
  let moreBtnEl = $state<HTMLButtonElement | null>(null); // 닫힘 시 포커스 복귀 대상(M5-A D3)
  function closeMore(): void {
    moreOpen = false;
  }
  // 메뉴 열림 동안 바깥클릭/Esc 로 닫기. Svelte 이펙트는 비동기 실행이라 여는 클릭 이후에 리스너가
  // 붙어 즉시 닫힘이 없음(+토글버튼은 moreEl 안이라 contains 가드로도 안전).
  $effect(() => {
    if (!moreOpen) return;
    const onDoc = (e: MouseEvent) => {
      if (moreEl && !moreEl.contains(e.target as Node)) moreOpen = false;
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') moreOpen = false;
    };
    document.addEventListener('click', onDoc);
    document.addEventListener('keydown', onKey);
    return () => {
      document.removeEventListener('click', onDoc);
      document.removeEventListener('keydown', onKey);
      // 닫힐 때 포커스를 ⋮ 로 복귀 — 메뉴 항목 언마운트로 포커스가 body 로 유실되는 것 방지(보이는 경우만).
      if (moreBtnEl?.offsetParent) moreBtnEl.focus();
    };
  });
  // 도구 이벤트 id → 트랜스크립트 항목 id. 같은 턴(send→approve) 동안 유지(승인 후 tool.result 매칭).
  let toolIds = new Map<string, string>();
  // 계획 스텝 추적(턴 스코프): 분석 스텝 id + 도구이벤트 id → 계획 스텝 id.
  let planAnalyzeId = '';
  let planToolIds = new Map<string, string>();

  onMount(async () => {
    if (!API_BASE) return;
    try {
      agents.load(await fetchAgents(API_BASE));
    } catch {
      /* 시드 유지 */
    }
    try {
      threads.load(await listThreads(API_BASE));
      threads.setActive(client.currentThread());
    } catch {
      /* 빈 목록 */
    }
    try {
      const [list, active] = await Promise.all([getModels(API_BASE), getSettingsModel(API_BASE)]);
      if (list.length) models.load(list, active);
    } catch {
      /* mock 프리셋 유지 */
    }
  });

  async function refreshThreads() {
    if (!API_BASE) return;
    try {
      threads.load(await listThreads(API_BASE));
      threads.setActive(client.currentThread());
    } catch {
      /* 무시 */
    }
  }

  async function newChat() {
    if (busy || awaiting) return;
    transcript.reset();
    citations.clear();
    plan.reset();
    awaiting = null;
    status = '';
    summaries = [];
    showSummary = false;
    if (API_BASE) {
      try {
        const id = await createThread(API_BASE);
        client.setThread(id);
        threads.prepend({ id, title: null, updated_at: new Date().toISOString() });
      } catch {
        /* 무시 */
      }
    } else {
      client.setThread(null);
    }
    await follow(true);
  }

  // 드로어에서 탭 → 드로어를 먼저 닫고 동작 수행(R14). selectThread/newChat 은 busy·awaiting 시
  // 조기 반환하므로 함수 진입부가 아니라 이 래퍼에서 닫아야 항상 닫힌다. rename/delete 는 목록 관리
  // 중이므로 닫지 않는다.
  function selectThreadNav(id: string): void {
    closeNav(); // 드로어를 먼저 닫고(R14) — selectThread 는 busy·awaiting 시 조기 반환하므로 래퍼에서 닫음
    void selectThread(id);
  }
  function newChatNav(): void {
    closeNav();
    void newChat();
  }

  // ≤760 진입/이탈 추적 + 와이드 복귀 시 드로어 강제 닫기(R14 — 회전·리사이즈로 데스크톱 폭이 되면
  // 떠 있던 드로어가 본문을 가리지 않게). 초기 본문은 navOpen 을 읽지 않아(자기참조 재구독 회피),
  // navOpen 참조는 추적 밖인 change 핸들러에서만.
  $effect(() => {
    const mq = window.matchMedia('(max-width: 760px)');
    isNarrow = mq.matches;
    const onChange = () => {
      isNarrow = mq.matches;
      if (!mq.matches && navOpen) closeNav();
    };
    mq.addEventListener('change', onChange);
    return () => mq.removeEventListener('change', onChange);
  });

  // 드로어 열림 동안: Esc 닫기(R13: 하드웨어 뒤로가기는 SvelteKit 라우터가 처리) + 포커스 진입/복귀(R8).
  $effect(() => {
    if (!navOpen) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        e.preventDefault();
        closeNav();
      }
    };
    window.addEventListener('keydown', onKey);
    // 포커스를 드로어 안 첫 포커서블로(role=dialog 진입). 셀렉터는 넉넉히(input/textarea/[tabindex] 포함, 교차검증 A5).
    void tick().then(() =>
      document
        .getElementById('thread-drawer')
        ?.querySelector<HTMLElement>(
          'button, [href], input, textarea, select, [tabindex]:not([tabindex="-1"])'
        )
        ?.focus()
    );
    return () => {
      window.removeEventListener('keydown', onKey);
      // 포커스를 햄버거로 복귀 — 단, 데스크톱 복귀로 닫힌 경우 햄버거가 display:none 이라 focus 가 body 로
      // 유실됨 → 보이는 경우(offsetParent 존재)에만 복귀(교차검증 A2).
      if (hamburgerEl?.offsetParent) hamburgerEl.focus();
    };
  });

  async function selectThread(id: string) {
    if (busy || awaiting || id === threads.activeId) return;
    client.setThread(id);
    threads.setActive(id);
    transcript.reset();
    citations.clear();
    plan.reset();
    awaiting = null;
    status = '';
    summaries = [];
    showSummary = false;
    if (API_BASE) {
      try {
        const [msgs, cits] = await Promise.all([getMessages(API_BASE, id), getCitations(API_BASE, id)]);
        transcript.loadMessages(msgs);
        citations.loadMany(cits);
      } catch {
        /* 무시 */
      }
    }
    await follow(true);
  }

  // 대화명 변경 — API 갱신 후 목록 즉시 반영(빈 제목은 무시).
  async function renameThreadHandler(id: string, title: string) {
    const t = title.trim();
    if (!t) return;
    if (API_BASE) {
      try {
        await renameThread(API_BASE, id, t);
      } catch (e) {
        status = e instanceof Error ? e.message : '이름 변경 실패';
        return;
      }
    }
    threads.rename(id, t);
  }

  // 대화 삭제 — 실행 중이면 차단(백엔드도 409). 활성 스레드 삭제 시 화면 초기화.
  async function deleteThreadHandler(id: string) {
    const wasActive = threads.activeId === id;
    if (wasActive && (busy || awaiting)) {
      status = '실행 중에는 삭제할 수 없습니다.';
      return;
    }
    if (API_BASE) {
      try {
        await deleteThread(API_BASE, id);
      } catch (e) {
        status = e instanceof Error ? e.message : '삭제 실패';
        return;
      }
    }
    threads.remove(id);
    if (wasActive) {
      client.setThread(null);
      transcript.reset();
      citations.clear();
      plan.reset();
      awaiting = null;
      status = '';
      summaries = [];        // 교차검증 F-2: 삭제된 스레드 요약 패널 잔류 방지
      showSummary = false;
    }
  }

  // 분기 — 백엔드 message id 에서 새 thread 생성 후 전환(이력 로드된 메시지에서만 backendId 존재).
  async function forkThreadHandler(messageId: string) {
    if (busy || awaiting || !API_BASE) return;
    const cur = client.currentThread();
    if (!cur) return;
    try {
      const newId = await forkThread(API_BASE, cur, messageId);
      await refreshThreads();
      await selectThread(newId);
    } catch (e) {
      status = e instanceof Error ? e.message : '분기 실패';
    }
  }

  // 현재 대화 전체 요약 생성 후 패널 표시.
  async function doSummarize() {
    const cur = client.currentThread();
    if (!cur || !API_BASE || summaryBusy || busy || awaiting) return; // 승인 대기 중엔 요약 금지(교차검증 F-1)
    summaryBusy = true;
    status = '요약 중…';
    try {
      await summarizeThread(API_BASE, cur);
      summaries = await getSummaries(API_BASE, cur);
      showSummary = true;
    } catch (e) {
      status = e instanceof Error ? e.message : '요약 실패';
    } finally {
      summaryBusy = false;
      if (status === '요약 중…') status = '';
    }
  }

  function toggleTheme() {
    theme = theme === 'light' ? 'dark' : 'light';
    document.documentElement.dataset.theme = theme;
  }

  function atBottom(): boolean {
    if (!scroller) return true;
    return scroller.scrollHeight - scroller.scrollTop - scroller.clientHeight < 80;
  }
  async function follow(force = false): Promise<void> {
    const stick = force || atBottom();
    await tick();
    if (stick) scroller?.scrollTo({ top: scroller.scrollHeight });
  }

  // 이벤트 스트림 소비 — send 와 approve 가 공유.
  async function consume(events: AsyncIterable<AgentEvent>): Promise<void> {
    try {
      for await (const ev of events) {
        switch (ev.type) {
          case 'run.started':
            if (ev.model) runModel = ev.model; // 실제 라우팅된 모델(선택값과 일치하는지 확인용)
            break;
          case 'tool.call':
            status = ev.name === 'search_legal' ? '법령 검색 중…' : `${ev.name} 실행 중…`;
            if (planAnalyzeId) plan.set(planAnalyzeId, 'done');
            planToolIds.set(ev.id, plan.add(`도구 · ${ev.name}`, 'active'));
            toolIds.set(ev.id, transcript.toolCall(ev.name, ev.args));
            break;
          case 'tool.result': {
            const pid = planToolIds.get(ev.id);
            if (pid) plan.set(pid, 'done');
            transcript.toolResult(toolIds.get(ev.id) ?? '', ev.content);
            status = '답변 작성 중…'; // 도구 결과 후 LLM 최종답변 생성 단계 표시
            break;
          }
          case 'citation.added':
            citations.add({ id: ev.id, kind: ev.kind, title: ev.title, ref: ev.ref, snippet: ev.snippet, url: ev.url });
            break;
          case 'message.completed':
            plan.finishAll();
            plan.add('답변 작성', 'done');
            transcript.commit(transcript.startAgent(), ev.text);
            answered = true; // 답변 도착 — 인디케이터 숨김
            break;
          case 'approval.requested':
            // 드로어가 열린 채 승인이 오면 .center 가 inert → 그 안의 ApprovalBar 버튼까지 inert 되어
            // 승인/거절 불가 교착(교차검증 M4-D1). 승인 게이트가 항상 조작 가능하도록 드로어 강제 닫기.
            if (navOpen) closeNav();
            if (taskOpen) closeTask(); // 우측 패널도 닫아 승인 시트 노출(M3)
            awaiting = { detail: ev.detail ?? '도구 실행 승인이 필요합니다.', tools: ev.tools ?? [] };
            status = '승인 대기…';
            break;
          case 'error':
            plan.finishAll(); // 펄스 멈춤(미완이지만 진행 표시는 종료)
            // 중지로 인한 종료는 실패 아님 — 가짜 "연결 끊김" 에러를 답변으로 커밋하지 않음(교차검증 B#6).
            if (!interrupting)
              transcript.commit(transcript.startAgent(), ev.message ?? '응답을 완성하지 못했습니다. 다시 시도해 주세요.');
            answered = true;
            break;
          case 'run.done':
            plan.finishAll(); // 중지/완료 모두 진행 펄스 종료(완료는 message.completed 가 이미 호출 — 멱등, 교차검증 C#1)
            break;
        }
        await follow();
      }
    } catch {
      if (!interrupting)
        transcript.commit(transcript.startAgent(), '연결에 문제가 발생했습니다. 다시 시도해 주세요.');
    } finally {
      busy = false;
      interrupting = false;
      if (!awaiting) status = '';
      await follow();
    }
  }

  async function onsend(text: string) {
    if (busy || awaiting || summaryBusy) return; // 요약 중 전송 차단(status race, 교차검증 A2)
    answered = false;
    transcript.addUser(text);
    // 첫 질문이면 대화명 즉시 반영(백엔드 set_*_if_empty 와 정합: 첫 비어있지않은 줄·코드포인트 60자·title==null).
    const curThread = client.currentThread();
    const firstLine = text.split('\n').map((l) => l.trim()).find((l) => l) ?? '';
    if (curThread && firstLine) {
      const th = threads.threads.find((x) => x.id === curThread);
      // title==null 만(빈문자는 백엔드 IS NULL 이 안 덮으므로 FE 도 동일). 코드포인트 슬라이스(이모지 분할 방지).
      if (th && th.title == null) threads.rename(curThread, [...firstLine].slice(0, 60).join(''));
    }
    plan.reset();
    planAnalyzeId = plan.add('질문 이해', 'active');
    planToolIds = new Map();
    toolIds = new Map();
    busy = true;
    status = '분석 중…';
    await follow(true);
    // 선택 모델을 이 run 에 명시 전송(per-run override). API_BASE 일 때만(mock 은 무시).
    await consume(client.send(text, API_BASE ? models.activeId : undefined));
    await refreshThreads(); // 새 스레드·제목·갱신순 반영(첫 전송 시 스레드 등장)
  }

  // 진행 중 run 중지 — backend 협조취소 → 스트림이 terminal 받고 consume 가 busy=false 처리.
  function stop() {
    if (!busy || awaiting) return;
    interrupting = true; // 이후 도착하는 run.done/error 를 정상 중지로 처리(가짜 에러 억제)
    status = '중지하는 중…';
    void client.interrupt();
  }

  async function decide(approve: boolean, approved?: string[]) {
    if (!awaiting) return;
    awaiting = null;
    busy = true;
    status = approve ? '계속…' : '거절됨';
    answered = false;
    await consume(client.approve(approve, approved));
  }
</script>

<div class="shell" style={agents.active.accent ? `--accent:${agents.active.accent}` : ''}>
  <header inert={navOpen || taskOpen || (!!awaiting && isNarrow) || undefined}>
    <button
      class="hamburger"
      bind:this={hamburgerEl}
      onclick={openNav}
      aria-label="대화 목록 열기"
      aria-haspopup="dialog"
      aria-controls="thread-drawer"
      aria-expanded={navOpen}
    >☰</button>
    <span class="brand">업무 에이전트</span>
    <span class="active">· {agents.active.label}</span>
    <div class="spacer"></div>
    <!-- 계획·근거 패널 토글 — ≤1040(태블릿+모바일)서 우측 드로어로 여닫음. 근거 수 배지로 발견성↑. -->
    <button
      class="task-toggle"
      bind:this={taskBtnEl}
      onclick={openTask}
      aria-haspopup="dialog"
      aria-controls="task-drawer"
      aria-expanded={taskOpen}
      aria-label="계획·근거 패널 열기"
    >근거{#if citations.items.length}<span class="tt-badge">{citations.items.length > 99 ? '99+' : citations.items.length}</span>{/if}</button>
    {#if status}<span class="status">{status}</span>{/if}
    {#if runModel}
      <!-- 실제 사용 모델 — mismatch(선택≠실행) 경고는 모바일서도 절대 숨김 금지(R4). 일치 시엔 모바일 숨김. -->
      <span
        class="ran"
        class:mismatch={!!models.activeId && runModel !== models.activeId}
        title="직전 응답에 실제 사용된 모델"
      >
        실행: {runModel}
      </span>
    {/if}
    <!-- 모델 칩은 상시 노출(매 턴 결정·신뢰 신호, R4) -->
    <ModelSelector models={models.models} activeId={models.activeId} onselect={selectModel} />
    <!-- 데스크톱: 인라인 / 모바일: ⋮ 더보기로 이동 -->
    <span class="chip desktop-only">검토 0</span>
    <button class="hbtn desktop-only" onclick={doSummarize} disabled={summaryBusy || !!awaiting} title="대화 요약">요약</button>
    <button class="theme desktop-only" onclick={toggleTheme} aria-label="테마 전환">{theme === 'light' ? '다크' : '라이트'}</button>
    <!-- 모바일 더보기 메뉴(요약·테마·검토) -->
    <!-- disclosure 패턴(메뉴 위젯 아님): role=menu 는 화살표키 내비를 약속하므로 부적합 → 단순 토글 +
         자연 Tab + Esc + 바깥클릭 + 닫힘 시 ⋮ 로 포커스 복귀(교차검증 M5-A D1/D3). -->
    <div class="more mobile-only" bind:this={moreEl}>
      <button
        class="more-btn"
        bind:this={moreBtnEl}
        onclick={() => (moreOpen = !moreOpen)}
        aria-haspopup="true"
        aria-expanded={moreOpen}
        aria-label="더보기"
      >⋮</button>
      {#if moreOpen}
        <div class="more-menu">
          <button
            class="more-item"
            onclick={() => {
              closeMore();
              void doSummarize();
            }}
            disabled={summaryBusy || !!awaiting}
          >대화 요약</button>
          <button
            class="more-item"
            onclick={() => {
              closeMore();
              toggleTheme();
            }}
          >{theme === 'light' ? '다크 모드' : '라이트 모드'}</button>
          <span class="more-item static" aria-hidden="true">검토 0</span>
        </div>
      {/if}
    </div>
  </header>

  <!-- status 토스트(모바일) — 시각 전용(aria-hidden): 진행 상태는 thinking 인디케이터(aria-live)·승인은
       alertdialog·에러는 transcript 가 이미 announce → 토스트가 또 live 면 이중 announce/churn(M5-A D5).
       승인 시트가 같은 상태를 덮으므로 awaiting 중엔 미표시(M5-C D5). -->
  {#if isNarrow && status && !awaiting}
    <div class="status-toast" aria-hidden="true">{status}</div>
  {/if}

  <div class="body" class:nav-open={navOpen} class:task-open={taskOpen}>
    <AgentRail agents={agents.agents} activeId={agents.activeId} onselect={(id: string) => agents.select(id)} inert={taskOpen} />
    <ThreadSidebar
      id="thread-drawer"
      dialog={isNarrow}
      inert={(isNarrow && !navOpen) || taskOpen}
      threads={threads.threads}
      activeId={threads.activeId}
      onselect={selectThreadNav}
      onnew={newChatNav}
      onrename={renameThreadHandler}
      ondelete={deleteThreadHandler}
      onclose={closeNav}
    />

    <!-- 좌/우 드로어 열림 시 본문은 inert — 포커스가 드로어를 벗어나 백드롭 뒤 본문에 닿지 않게(A1, aria-modal 정직성).
         단 awaiting 시엔 절대 inert 아님 — closeNav/closeTask 의 history.back 은 async 라 pop 전 1프레임 동안
         taskOpen/navOpen 이 남아 .center(→ 그 안 ApprovalBar) 가 inert 되는 M4-D1 교착 재현 방지(M3-A D3). -->
    <section class="center" inert={((navOpen || taskOpen) && !awaiting) || undefined}>
      {#if showSummary && summaries.length}
        <!-- 승인 시트(모바일 모달) 동안 요약 패널도 inert — sp-close 가 scrim 뒤로 새지 않게(M4-D2). -->
        <div class="summary-panel" inert={(!!awaiting && isNarrow) || undefined}>
          <div class="sp-head">
            <span>대화 요약</span>
            <button class="sp-close" onclick={() => (showSummary = false)} aria-label="닫기">✕</button>
          </div>
          {#each [...summaries].reverse() as sm (sm.id)}
            <div class="sp-item">{sm.content_md}</div>
          {/each}
        </div>
      {/if}
      <!-- 승인 시트(모바일 모달) 동안 본문 inert — 포커스가 scrim 뒤 대화/칩으로 새지 않게(M4). -->
      <main bind:this={scroller} inert={(!!awaiting && isNarrow) || undefined}>
        <ConversationView items={transcript.items} citations={citations.items} onfork={forkThreadHandler} />
        {#if busy && !awaiting && !answered}
          <div class="thinking" aria-live="polite">
            <span class="t-avatar" aria-hidden="true">AI</span>
            <span class="t-label">{status || '생각 중'}</span>
            <span class="t-dots" aria-hidden="true"><i></i><i></i><i></i></span>
          </div>
        {/if}
      </main>
      {#if awaiting}
        <ApprovalBar
          detail={awaiting.detail}
          tools={awaiting.tools}
          sheet={isNarrow}
          onApprove={(approved) => decide(true, approved)}
          onReject={() => decide(false)}
        />
      {/if}
      <Composer {onsend} busy={busy || !!awaiting} stoppable={busy && !awaiting} onstop={stop} />
    </section>

    <TaskSidebar
      id="task-drawer"
      dialog={isTabletDown}
      inert={isTabletDown && !taskOpen}
      onclose={closeTask}
      steps={plan.steps}
      citations={citations.items}
    />
  </div>

  <!-- 드로어 백드롭(R7: .body 그리드 밖이라 트랙 오염 없음). 탭하면 닫힘. 좌(nav)·우(task) 공용 스타일. -->
  {#if navOpen}
    <button class="backdrop" onclick={closeNav} aria-label="대화 목록 닫기" tabindex="-1"></button>
  {/if}
  {#if taskOpen}
    <button class="backdrop" onclick={closeTask} aria-label="계획·근거 패널 닫기" tabindex="-1"></button>
  {/if}
</div>

<style>
  /* 높이: 100dvh — interactive-widget=resizes-content 로 키보드 시 뷰포트 축소를 따라감(M7/P11 선언적). */
  .shell { display: grid; grid-template-rows: auto 1fr; height: 100vh; height: 100dvh; background: var(--bg); }
  header {
    display: flex; align-items: center; gap: 8px; flex-wrap: nowrap;
    padding: max(9px, env(safe-area-inset-top)) max(14px, env(safe-area-inset-right)) 9px max(14px, env(safe-area-inset-left));
    border-bottom: 0.5px solid var(--border);
  }
  /* 좁은 화면서 brand 가 줄어들어 ⋮ 가 화면 밖으로 밀리지 않게(M5-C D2) */
  .brand { font-weight: 500; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .active { font-size: 13px; color: var(--accent); }
  .spacer { flex: 1; }
  .status { font-size: 12px; color: var(--accent); margin-right: 4px; }
  .ran {
    font-size: 11px; color: var(--text-faint);
    border: 0.5px solid var(--border); border-radius: var(--r-md); padding: 2px 7px;
    max-width: 220px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
  }
  .ran.mismatch { color: var(--danger, #d9534f); border-color: var(--danger, #d9534f); }
  .chip {
    font-size: 11.5px; color: var(--text-soft);
    border: 0.5px solid var(--border); border-radius: var(--r-md); padding: 2px 8px;
  }
  /* ── 반응형 M5: 헤더 응축 — ⋮ 더보기 메뉴 + status 토스트 ── */
  .mobile-only { display: none; } /* 데스크톱 기본 숨김(≤760서 노출) */
  .more { position: relative; flex: 0 0 auto; } /* ⋮ 는 절대 수축/탈락 안 함(M5-C D3) */
  .more-btn {
    width: 32px; height: 32px; border-radius: var(--r-md);
    border: 0.5px solid var(--border); background: var(--bg); color: var(--text);
    cursor: pointer; font-size: 16px; line-height: 1;
  }
  .more-btn:hover { background: var(--bg-soft); }
  .more-menu {
    position: absolute; right: 0; top: calc(100% + 6px); z-index: 70;
    display: flex; flex-direction: column; min-width: 168px;
    background: var(--bg); border: 0.5px solid var(--border-strong);
    border-radius: var(--r-md); box-shadow: 0 4px 16px rgba(0, 0, 0, 0.16);
    padding: 4px; gap: 1px;
  }
  .more-item {
    text-align: left; padding: 11px 12px; min-height: 44px; font-size: 14px;
    border: none; background: transparent; color: var(--text); cursor: pointer;
    border-radius: var(--r-sm);
  }
  .more-item:hover:not(:disabled):not(.static) { background: var(--bg-soft); }
  .more-item:disabled { opacity: 0.5; cursor: default; }
  .more-item.static { color: var(--text-soft); cursor: default; }
  .status-toast {
    position: fixed; left: 50%; transform: translateX(-50%);
    top: calc(env(safe-area-inset-top) + 54px); z-index: 40;
    max-width: 90vw; padding: 5px 13px; border-radius: 999px;
    /* 헤더(패딩 max(9,sat)+내용~30+9 ≈ sat+48) 아래로 — 비노치(sat=0)서도 겹치지 않게 +54px 여유(M5-C D4) */
    background: var(--text); color: var(--bg); font-size: 12px; line-height: 1.4;
    white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
    box-shadow: 0 2px 10px rgba(0, 0, 0, 0.22);
  }
.thinking { display: flex; align-items: center; gap: 10px; padding: 2px 16px 16px; }
  .thinking .t-avatar {
    flex: 0 0 26px; width: 26px; height: 26px; border-radius: 50%;
    display: flex; align-items: center; justify-content: center;
    font-size: 11px; background: var(--accent-soft); color: var(--accent);
  }
  .thinking .t-label { font-size: 13px; color: var(--text-soft); }
  .thinking .t-dots { display: inline-flex; gap: 3px; }
  .thinking .t-dots i { width: 5px; height: 5px; border-radius: 50%; background: var(--text-faint); animation: blink 1.2s infinite both; }
  .thinking .t-dots i:nth-child(2) { animation-delay: 0.2s; }
  .thinking .t-dots i:nth-child(3) { animation-delay: 0.4s; }
  @keyframes blink { 0%, 80%, 100% { opacity: 0.2; } 40% { opacity: 1; } }
  .summary-panel {
    margin: 10px 12px 0; border: 0.5px solid var(--border-strong); border-radius: var(--r-md);
    background: var(--bg-soft); max-height: 30vh; overflow-y: auto; font-size: 12.5px;
  }
  .sp-head {
    display: flex; align-items: center; justify-content: space-between;
    padding: 6px 10px; border-bottom: 0.5px solid var(--border); color: var(--text-soft); font-weight: 500;
    position: sticky; top: 0; background: var(--bg-soft);
  }
  .sp-close { border: none; background: transparent; cursor: pointer; color: var(--text-faint); font-size: 13px; line-height: 1; }
  .sp-close:hover { color: var(--text); }
  .sp-item { padding: 8px 10px; white-space: pre-wrap; line-height: 1.6; color: var(--text); border-top: 0.5px solid var(--border); overflow-wrap: anywhere; }
  .sp-item:first-of-type { border-top: none; }
    .theme {
    font-size: 11.5px; color: var(--text-soft); cursor: pointer;
    border: 0.5px solid var(--border); border-radius: var(--r-md); padding: 3px 9px; background: var(--bg);
  }
  .theme:hover { background: var(--bg-soft); }

  /* 햄버거 — 데스크톱 숨김, 모바일(≤760)서만 노출(대화목록 드로어 토글, M2). */
  .hamburger {
    display: none; flex: 0 0 auto; align-items: center; justify-content: center;
    width: 34px; height: 34px; border: 0.5px solid var(--border-strong);
    border-radius: var(--r-md); background: var(--bg); color: var(--text);
    cursor: pointer; font-size: 16px; line-height: 1;
  }
  .hamburger:hover { background: var(--bg-soft); }
  /* 드로어 백드롭 — 데스크톱엔 안 뜨지만(navOpen 리셋) 안전망으로 폭에서도 가림. */
  .backdrop {
    position: fixed; inset: 0; z-index: 45; border: none; cursor: pointer;
    background: rgba(0, 0, 0, 0.42); animation: backdrop-in 0.18s ease;
  }
  @keyframes backdrop-in { from { opacity: 0; } to { opacity: 1; } }

  /* 계획·근거 패널 토글 — 데스크톱(>1040)은 패널이 상시 컬럼이라 숨김, ≤1040 서만 노출(M3) */
  .task-toggle {
    display: none; align-items: center; gap: 4px; flex: 0 0 auto;
    height: 32px; padding: 0 10px; border-radius: var(--r-md);
    border: 0.5px solid var(--border); background: var(--bg); color: var(--text-soft);
    cursor: pointer; font-size: 12px; line-height: 1;
  }
  .task-toggle:hover { background: var(--bg-soft); }
  .tt-badge {
    min-width: 16px; height: 16px; padding: 0 4px; border-radius: 999px;
    display: inline-flex; align-items: center; justify-content: center;
    background: var(--accent); color: #fff; font-size: 10px; line-height: 1;
  }

  .body { display: grid; grid-template-columns: 52px 190px minmax(0, 1fr) 300px; min-height: 0; }
  .center { display: flex; flex-direction: column; min-height: 0; border-right: 0.5px solid var(--border); }
  main { flex: 1; overflow-y: auto; }

  @media (max-width: 1040px) {
    /* AgentRail 은 태블릿 이하 숨김(R5/P6 — 에이전트 1종이라 저가치, 새 대화는 ThreadSidebar 에 있음). */
    .body { grid-template-columns: 190px minmax(0, 1fr); }
    .body :global(.rail) { display: none; }
    .task-toggle { display: inline-flex; } /* ≤1040: 패널 토글 노출 */
    /* TaskSidebar → 우측 오프캔버스 드로어(M3). position:fixed 라 그리드 트랙 미점유(center 풀폭 유지). */
    .body :global(.task) {
      position: fixed; top: 0; bottom: 0; right: 0; z-index: 50;
      width: min(86vw, 320px); transform: translateX(100%);
      transition: transform 0.22s ease; box-shadow: -2px 0 16px rgba(0, 0, 0, 0.18);
      padding-top: env(safe-area-inset-top);
      padding-bottom: env(safe-area-inset-bottom);
      padding-right: env(safe-area-inset-right); /* 가로모드 노치 — 우측 드로어는 우측 인셋 직접 적용(M3-C D2) */
    }
    .body.task-open :global(.task) { transform: translateX(0); }
    /* landscape 노치 대비 가로 safe-area(≤1040): 본문 스크롤 영역과, rail 이 사라져 좌측 가장자리로 온
       태블릿 static threads 컬럼에 좌/우 인셋. 세로/노치없음이면 env=0 이라 무영향(M7-D1/D2). */
    main { padding-left: env(safe-area-inset-left); padding-right: env(safe-area-inset-right); }
    .body :global(.threads) { padding-left: env(safe-area-inset-left); }
  }
  @media (max-width: 760px) {
    .hamburger { display: inline-flex; }
    /* 헤더 응축(M5/R4): 요약·테마·검토는 ⋮ 로, active 라벨/인라인 status 는 숨김(status 는 토스트로 대체). */
    .desktop-only { display: none; }
    .mobile-only { display: block; } /* revert(UA 기본 의존) 대신 명시(M5-C D1) */
    .active { display: none; }
    .status { display: none; }
    .ran:not(.mismatch) { display: none; } /* mismatch 경고만 상시 — 일치 시 숨김(R4) */
    .ran.mismatch { max-width: 90px; } /* 좁은 화면서 경고가 우측 버튼들(근거·⋮)을 밀지 않게(M5-C D2/M3-C D3) */
    /* 본문 한 칸(레일은 이미 @1040 서 숨김). 태스크/대화목록은 오프캔버스라 그리드 트랙 미점유. */
    .body { grid-template-columns: minmax(0, 1fr); }
    /* ThreadSidebar → 좌측 오프캔버스 드로어(R2/R7). position:fixed 라 그리드 흐름서 빠짐. */
    .body :global(.threads) {
      position: fixed; top: 0; bottom: 0; left: 0; z-index: 50;
      width: min(82vw, 300px); transform: translateX(-100%);
      transition: transform 0.22s ease; box-shadow: 2px 0 16px rgba(0, 0, 0, 0.18);
      /* 노치/홈인디케이터 회피 — 드로어는 top:0/bottom:0 라 헤더 safe-area 를 못 받으므로 직접 적용(C-D1). */
      padding-top: env(safe-area-inset-top);
      padding-bottom: env(safe-area-inset-bottom);
      padding-left: env(safe-area-inset-left); /* 가로모드 좌측 노치(M7 landscape) */
    }
    .body.nav-open :global(.threads) { transform: translateX(0); }
  }
  @media (prefers-reduced-motion: reduce) {
    .body :global(.threads), .body :global(.task) { transition: none; } /* 좌·우 드로어 패리티(M3-C D1) */
    .backdrop { animation: none; }
  }
  /* 데스크톱(>760) 복귀 시 떠 있을 수 있는 백드롭 차단(R14 안전망). */
  @media (min-width: 761px) {
    .backdrop { display: none; }
  }
</style>
