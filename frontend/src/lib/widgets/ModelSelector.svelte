<script lang="ts">
  // 로컬↔GPT 모델 셀렉터(헤더). 프레젠테이션 전용 — 목록/선택은 페이지가 소유.
  import type { ModelInfo } from '$lib/shared/api/models';

  interface Props {
    models?: ModelInfo[];
    activeId?: string;
    onselect?: (id: string) => void;
  }
  let { models = [], activeId = '', onselect = () => {} }: Props = $props();

  function badge(p?: string): string {
    return p === 'openai' ? 'GPT' : p === 'compatible' ? '로컬' : '';
  }
</script>

{#if models.length}
  <label class="model" title="LLM 모델 선택">
    <span class="cpu" aria-hidden="true">▾</span>
    <select value={activeId} onchange={(e) => onselect((e.currentTarget as HTMLSelectElement).value)} aria-label="모델 선택">
      {#each models as m (m.id)}
        <option value={m.id}>{badge(m.provider) ? `${badge(m.provider)} · ` : ''}{m.id}</option>
      {/each}
    </select>
  </label>
{/if}

<style>
  .model {
    display: inline-flex; align-items: center; gap: 4px;
    border: 0.5px solid var(--border); border-radius: var(--r-md); padding: 1px 4px 1px 8px;
    font-size: 11.5px; color: var(--text-soft); background: var(--bg); cursor: pointer;
  }
  .model:hover { background: var(--bg-soft); }
  .cpu { font-size: 10px; color: var(--text-faint); }
  select {
    border: none; background: transparent; color: var(--text-soft);
    font: inherit; font-size: 11.5px; padding: 3px 2px; cursor: pointer; max-width: 200px;
  }
  select:focus { outline: none; }
</style>
