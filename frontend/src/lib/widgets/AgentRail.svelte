<script lang="ts">
  // 프레젠테이션 전용 — 프로파일 데이터/선택은 페이지(AgentStore)가 소유해 props 로 내려준다.
  import type { AgentProfile } from '$lib/entities/agent/types';

  // inert: 태블릿(≤1040)서 우측 task 드로어가 모달로 열릴 때 배경 레일을 비포커스화(M3 a11y).
  let {
    agents = [] as AgentProfile[],
    activeId = '',
    onselect = (_id: string) => {},
    inert: inertProp = false
  } = $props();
</script>

<nav class="rail" aria-label="에이전트" inert={inertProp || undefined}>
  <button class="new" title="새 작업" aria-label="새 작업">+</button>
  <div class="sep"></div>
  {#each agents as a (a.id)}
    <button
      class="ag"
      class:on={activeId === a.id}
      class:soon={!a.ready}
      title={a.label}
      aria-label={a.label}
      aria-pressed={activeId === a.id}
      disabled={!a.ready}
      onclick={() => a.ready && onselect(a.id)}
    >{a.abbr}</button>
  {/each}
  <div class="grow"></div>
  <button class="gear" title="설정" aria-label="설정">
    <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" aria-hidden="true">
      <circle cx="12" cy="12" r="3" />
      <path d="M12 2v3M12 19v3M2 12h3M19 12h3M4.9 4.9l2.1 2.1M17 17l2.1 2.1M19.1 4.9L17 7M7 17l-2.1 2.1" />
    </svg>
  </button>
</nav>

<style>
  .rail {
    display: flex; flex-direction: column; align-items: center; gap: 6px;
    padding: 10px 0; border-right: 0.5px solid var(--border); background: var(--bg);
  }
  button {
    width: 34px; height: 34px; border-radius: var(--r-md);
    border: 0.5px solid transparent; background: transparent; color: var(--text-soft);
    cursor: pointer; font-size: 12px; display: flex; align-items: center; justify-content: center;
  }
  button:hover { background: var(--bg-soft); }
  .new { font-size: 20px; color: var(--text-soft); }
  .ag { font-weight: 500; }
  .ag.on { background: var(--accent-soft); color: var(--accent); border-color: var(--accent-soft); }
  .ag.soon { opacity: 0.4; cursor: default; }
  .ag.soon:hover { background: transparent; }
  .gear { color: var(--text-faint); }
  .sep { width: 20px; height: 0.5px; background: var(--border); margin: 2px 0; }
  .grow { flex: 1; }
</style>
