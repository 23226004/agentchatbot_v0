<script lang="ts">
  // Codex 벤치마크 Task 사이드바 — 계획 / 출처 / 산출물 탭.
  import type { PlanStep } from '$lib/entities/plan/plan.svelte';
  import type { Citation } from '$lib/shared/api/contracts';
  import CitationList from '$lib/widgets/CitationList.svelte';

  interface Props {
    steps?: PlanStep[];
    citations?: Citation[];
    // 반응형 M3: ≤1040 서 우측 오프캔버스 드로어로 동작.
    id?: string; // aria-controls 타깃 + 열림 시 포커스 진입용
    inert?: boolean; // 닫힌 드로어 비활성(off-screen 비포커스, R8)
    dialog?: boolean; // 드로어(모달) 모드 → role=dialog/aria-modal + 패널 내 닫기 버튼
    onclose?: () => void;
  }
  let {
    steps = [],
    citations = [],
    id = undefined,
    inert: inertProp = false,
    dialog = false,
    onclose = () => {}
  }: Props = $props();

  type Tab = 'plan' | 'sources' | 'artifacts';
  let tab = $state<Tab>('plan');
</script>

<aside
  class="task"
  {id}
  inert={inertProp || undefined}
  role={dialog ? 'dialog' : undefined}
  aria-modal={dialog ? 'true' : undefined}
  aria-label={dialog ? '계획·근거 패널' : undefined}
>
  {#if dialog}
    <!-- 드로어(모달) 모드에서만 — 패널 내 명시적 닫기(키보드/AT 접근 가능, M2/M3). -->
    <button class="drawer-close" onclick={onclose} aria-label="계획·근거 패널 닫기">✕ 닫기</button>
  {/if}
  <div class="tabs" role="tablist">
    <button class:on={tab === 'plan'} role="tab" aria-selected={tab === 'plan'} onclick={() => (tab = 'plan')}>계획</button>
    <button class:on={tab === 'sources'} role="tab" aria-selected={tab === 'sources'} onclick={() => (tab = 'sources')}>
      출처{#if citations.length} {citations.length}{/if}
    </button>
    <button class:on={tab === 'artifacts'} role="tab" aria-selected={tab === 'artifacts'} onclick={() => (tab = 'artifacts')}>산출물</button>
  </div>

  <div class="body">
    {#if tab === 'plan'}
      {#if steps.length}
        {#each steps as s (s.id)}
          <div class="step {s.status}">
            <span class="dot"></span>{s.label}
          </div>
        {/each}
      {:else}
        <p class="empty">실행 계획이 여기에 표시됩니다.</p>
      {/if}
    {:else if tab === 'sources'}
      {#if citations.length}
        <CitationList {citations} bare />
      {:else}
        <p class="empty">인용된 근거가 없습니다.</p>
      {/if}
    {:else}
      <p class="empty">생성된 산출물이 없습니다.</p>
    {/if}
  </div>
</aside>

<style>
  .task { display: flex; flex-direction: column; border-left: 0.5px solid var(--border); background: var(--bg); min-height: 0; }
  .drawer-close {
    margin: 8px 10px 2px; padding: 10px 12px; min-height: 44px; text-align: left;
    border: 0.5px solid var(--border-strong); border-radius: var(--r-md);
    background: var(--bg); color: var(--text-soft); cursor: pointer; font-size: 14px;
  }
  .drawer-close:hover { background: var(--bg-soft); }
  .tabs { display: flex; gap: 4px; padding: 10px 10px 8px; border-bottom: 0.5px solid var(--border); }
  .tabs button {
    font-size: 12px; padding: 4px 9px; border-radius: var(--r-md);
    border: none; background: transparent; color: var(--text-faint); cursor: pointer;
  }
  .tabs button:hover { background: var(--bg-soft); }
  .tabs button.on { background: var(--accent-soft); color: var(--accent); }
  .body { flex: 1; overflow-y: auto; padding: 10px 12px; }
  .empty { font-size: 12px; color: var(--text-faint); margin: 8px 2px; line-height: 1.6; }
  .step { display: flex; align-items: center; gap: 8px; font-size: 12.5px; padding: 5px 0; color: var(--text-soft); }
  .step .dot { width: 11px; height: 11px; border-radius: 50%; border: 1.5px solid var(--border-strong); flex: 0 0 auto; }
  .step.active { color: var(--accent); }
  .step.active .dot { border-color: var(--accent); box-shadow: inset 0 0 0 2px var(--accent); animation: pulse 1.2s ease-in-out infinite; }
  .step.done { color: var(--text); }
  .step.done .dot { border-color: var(--success); background: var(--success); }
  @keyframes pulse { 0%,100% { opacity: 1; } 50% { opacity: 0.4; } }
</style>
