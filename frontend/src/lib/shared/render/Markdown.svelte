<script lang="ts">
  // 안전 렌더 마크다운 + 인용 칩. {@html} 대상은 DOMPurify 로 새니타이즈됨.
  import { renderMarkdown } from '$lib/shared/render/markdown';
  import type { Citation } from '$lib/shared/api/contracts';

  interface Props {
    source?: string;
    citations?: Citation[];
  }
  let { source = '', citations = [] }: Props = $props();

  let el = $state<HTMLElement | null>(null);
  let html = $derived(renderMarkdown(source));
  let citeMap = $derived(new Map(citations.map((c) => [c.id, c])));

  // 렌더 후 cite 칩에 라벨(ref)·툴팁(title) 주입. html/citeMap 변경 시 재실행.
  $effect(() => {
    void html;
    void citeMap;
    const root = el;
    if (!root) return;
    for (const node of root.querySelectorAll<HTMLElement>('sup.cite[data-cite]')) {
      const id = node.dataset.cite ?? '';
      const c = citeMap.get(id);
      node.textContent = c?.ref ?? id;
      if (c?.title) node.title = c.title;
    }
  });
</script>

<div class="md" bind:this={el}>{@html html}</div>

<style>
  .md { font-size: 14px; line-height: 1.8; color: var(--text); }
  .md :global(p) { margin: 0 0 10px; }
  .md :global(p:last-child) { margin-bottom: 0; }
  .md :global(h1), .md :global(h2), .md :global(h3) { font-weight: 500; line-height: 1.3; margin: 14px 0 8px; }
  .md :global(h1) { font-size: 18px; }
  .md :global(h2) { font-size: 16px; }
  .md :global(h3) { font-size: 15px; }
  .md :global(ul), .md :global(ol) { margin: 0 0 10px; padding-left: 20px; }
  .md :global(li) { margin: 2px 0; }
  .md :global(a) { color: var(--accent); text-decoration: none; }
  .md :global(a:hover) { text-decoration: underline; }
  .md :global(code) {
    font-family: var(--mono); font-size: 12.5px;
    background: var(--bg-inset); border-radius: var(--r-sm); padding: 1px 5px;
  }
  .md :global(pre) { background: var(--bg-inset); border-radius: var(--r-md); padding: 10px 12px; overflow-x: auto; }
  .md :global(pre code) { background: none; padding: 0; }
  .md :global(blockquote) { margin: 0 0 10px; padding: 2px 12px; border-left: 3px solid var(--border-strong); color: var(--text-soft); }
  .md :global(table) { border-collapse: collapse; font-size: 13px; margin: 0 0 10px; }
  .md :global(th), .md :global(td) { border: 0.5px solid var(--border); padding: 5px 9px; text-align: left; }
  .md :global(th) { background: var(--bg-soft); font-weight: 500; }
  .md :global(sup.cite) {
    font-size: 10px; color: var(--accent); background: var(--accent-soft);
    border-radius: var(--r-sm); padding: 0 4px; margin: 0 1px; vertical-align: 1px; cursor: default;
  }
</style>
