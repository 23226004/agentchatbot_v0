<script lang="ts">
  import type { Citation } from '$lib/shared/api/contracts';

  // bare=true → 자체 헤더/테두리 없이 항목만(Task 사이드바 출처 탭 내부용).
  let { citations = [] as Citation[], bare = false } = $props();
</script>

{#if citations.length}
  <div class="sources" class:bare>
    {#if !bare}<div class="head">출처 {citations.length}</div>{/if}
    {#each citations as c (c.id)}
      <div class="item">
        <span class="ref">{c.ref}</span>
        {#if c.snippet}<span class="snip">· {c.snippet}</span>{/if}
        {#if c.url}<a href={c.url} target="_blank" rel="noreferrer" aria-label="원문">↗</a>{/if}
      </div>
    {/each}
  </div>
{/if}

<style>
  .sources { padding: 10px 16px; border-top: 0.5px solid var(--border); background: var(--bg-soft); }
  .sources.bare { padding: 0; border-top: none; background: transparent; }
  .head { font-size: 11px; color: var(--text-faint); margin-bottom: 6px; }
  .item { font-size: 12px; color: var(--text-soft); display: flex; align-items: center; gap: 6px; padding: 3px 0; }
  .ref { color: var(--accent); font-weight: 500; }
  .snip { color: var(--text-soft); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  a { margin-left: auto; color: var(--accent); text-decoration: none; font-size: 12px; }
</style>
