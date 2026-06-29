<script lang="ts">
  // Zero-Trust HITL 게이트 — 위험 도구 실행 전 사용자 승인. per-tool 선택 실행(체크 해제=그 도구 거절).
  interface Tool {
    id: string;
    name: string;
    args?: Record<string, unknown>;
  }
  interface Props {
    detail?: string;
    tools?: Tool[];
    onApprove?: (approved?: string[]) => void; // approved 미지정=전체 승인, 지정=선택 실행
    onReject?: () => void;
  }
  let { detail = '', tools = [], onApprove = () => {}, onReject = () => {} }: Props = $props();

  // 기본 전체 선택 — 해제된 id 만 추적(unchecked). 체크 = !unchecked.has(id).
  let unchecked = $state(new Set<string>());
  const isChecked = (id: string) => !unchecked.has(id);
  function toggle(id: string) {
    const next = new Set(unchecked);
    if (next.has(id)) next.delete(id);
    else next.add(id);
    unchecked = next; // 재할당으로 반응성 트리거
  }
  const selectedIds = $derived(tools.filter((t) => !unchecked.has(t.id)).map((t) => t.id));

  function approve() {
    // 전체 선택이면 목록 없이(전체 승인 경로), 일부면 선택 목록 전달.
    onApprove(selectedIds.length === tools.length ? undefined : selectedIds);
  }
  function argsSummary(args?: Record<string, unknown>): string {
    if (!args) return '';
    try {
      return Object.entries(args)
        .map(([k, v]) => `${k}: ${typeof v === 'string' ? v : JSON.stringify(v)}`)
        .join(', ');
    } catch {
      return '';
    }
  }
</script>

<div class="bar" role="alertdialog" aria-label="실행 승인 요청">
  <div class="head">
    <span class="ico" aria-hidden="true">!</span>
    <span class="detail">{detail || '도구 실행을 승인하시겠습니까?'}</span>
  </div>

  {#if tools.length}
    <ul class="tools">
      {#each tools as t (t.id)}
        <li>
          <label>
            <input type="checkbox" checked={isChecked(t.id)} onchange={() => toggle(t.id)} />
            <span class="t-name">{t.name}</span>
            {#if argsSummary(t.args)}<span class="t-args">({argsSummary(t.args)})</span>{/if}
          </label>
        </li>
      {/each}
    </ul>
  {/if}

  <div class="actions">
    <button class="reject" onclick={() => onReject()}>거절</button>
    <button
      class="approve"
      onclick={approve}
      disabled={tools.length > 0 && selectedIds.length === 0}
    >
      {#if tools.length > 0 && selectedIds.length < tools.length}
        선택 실행 ({selectedIds.length})
      {:else}
        승인
      {/if}
    </button>
  </div>
</div>

<style>
  .bar {
    display: flex; flex-direction: column; gap: 8px;
    padding: 9px 12px; border-top: 0.5px solid var(--border);
    background: color-mix(in srgb, var(--warning) 10%, var(--bg));
  }
  .head { display: flex; align-items: center; gap: 10px; }
  .ico {
    flex: 0 0 auto; width: 18px; height: 18px; border-radius: 50%;
    display: flex; align-items: center; justify-content: center;
    background: var(--warning); color: var(--bg); font-size: 12px; font-weight: 500;
  }
  .detail { flex: 1; font-size: 12.5px; color: var(--text); min-width: 0; }
  .tools { list-style: none; margin: 0; padding: 0 0 0 28px; display: flex; flex-direction: column; gap: 3px; }
  .tools label { display: flex; align-items: baseline; gap: 6px; font-size: 12px; cursor: pointer; }
  .tools input { margin: 0; flex: 0 0 auto; align-self: center; cursor: pointer; }
  .t-name { color: var(--text); font-weight: 500; }
  .t-args { color: var(--text-faint); font-size: 11px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
  .actions { display: flex; gap: 6px; justify-content: flex-end; }
  .actions button { font-size: 12px; padding: 5px 12px; border-radius: var(--r-md); cursor: pointer; border: 0.5px solid var(--border-strong); }
  .reject { background: var(--bg); color: var(--text-soft); }
  .reject:hover { background: var(--bg-soft); }
  .approve { background: var(--accent); color: #fff; border-color: var(--accent); }
  .approve:hover:not(:disabled) { filter: brightness(1.05); }
  .approve:disabled { opacity: 0.5; cursor: default; }
</style>
