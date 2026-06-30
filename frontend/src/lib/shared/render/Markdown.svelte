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
  let selected = $state<Citation | null>(null); // 칩 탭 시 인라인 확장할 근거

  // http(s) URL 만 허용 — javascript:/data: 등 위험 스킴 차단(방어심층: 동적 href 는 DOMPurify·
  // Svelte 런타임 검증을 우회하므로 FE 에서 직접 화이트리스트, 교차검증 B).
  function safeUrl(u?: string): string | undefined {
    return u && /^https?:\/\//i.test(u.trim()) ? u : undefined;
  }

  // 렌더 후 cite 칩에 라벨(ref)·툴팁(title) 주입 + **인터랙티브화**(탭→근거 인라인 확장).
  // html/citeMap/selected 변경 시 재실행(aria-expanded 동적 갱신).
  $effect(() => {
    void html;
    void citeMap;
    void selected;
    const root = el;
    if (!root) return;
    // selected 근거가 라이브러리서 사라지면(스레드 전환 등) 카드 해제 — stale 방지(교차검증 D4).
    if (selected && !citeMap.has(selected.id)) {
      selected = null;
      return;
    }
    for (const node of root.querySelectorAll<HTMLElement>('sup.cite[data-cite]')) {
      const id = node.dataset.cite ?? '';
      const c = citeMap.get(id);
      node.textContent = c?.ref ?? id;
      if (c) {
        // 근거 있음 → 버튼처럼: 포커스·키보드·토글상태(모바일 hover 없음 대응).
        if (c.title) node.title = c.title;
        node.setAttribute('role', 'button');
        node.tabIndex = 0;
        node.setAttribute('aria-label', `근거 ${c.ref ?? id} — 펼쳐 보기`);
        node.setAttribute('aria-expanded', String(selected?.id === id));
        node.classList.add('clickable');
      } else {
        // 근거 없는 칩(도착 전·누락) → 인터랙티브 해제(stale tabindex/Space 스크롤 누수 방지, 교차검증 D2/D3).
        node.removeAttribute('role');
        node.removeAttribute('tabindex');
        node.removeAttribute('aria-expanded');
        node.classList.remove('clickable');
      }
    }
  });

  function citeOf(t: EventTarget | null): Citation | undefined {
    const n = t as HTMLElement | null;
    if (n && n.tagName === 'SUP' && n.classList?.contains('cite')) return citeMap.get(n.dataset.cite ?? '');
    return undefined;
  }
  function onChipClick(e: MouseEvent) {
    const c = citeOf(e.target);
    if (c) selected = selected?.id === c.id ? null : c; // 같은 칩 재탭=닫기
  }
  function onChipKey(e: KeyboardEvent) {
    if (e.key !== 'Enter' && e.key !== ' ') return;
    const c = citeOf(e.target);
    if (c) {
      e.preventDefault();
      selected = selected?.id === c.id ? null : c;
    }
  }
</script>

<!-- 인용 칩(sup.cite)은 {@html} 내부라 위임 이벤트로 처리. 실제 인터랙티브 요소는 칩(role=button·tabindex). -->
<!-- svelte-ignore a11y_no_static_element_interactions -->
<div class="md" bind:this={el} onclick={onChipClick} onkeydown={onChipKey}>{@html html}</div>
{#if selected}
  {@const url = safeUrl(selected.url)}
  <div class="cite-detail" role="note">
    <span class="cd-ref">{selected.ref ?? selected.id}</span>
    {#if selected.snippet}<span class="cd-snip">{selected.snippet}</span>{/if}
    {#if url}<a class="cd-link" href={url} target="_blank" rel="noopener noreferrer">원문 ↗</a>{/if}
    <button class="cd-close" onclick={() => (selected = null)} aria-label="근거 닫기">✕</button>
  </div>
{/if}

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
  .md :global(sup.cite.clickable) { cursor: pointer; }
  .md :global(sup.cite.clickable:hover) { filter: brightness(0.96); text-decoration: underline; }
  .md :global(sup.cite.clickable:focus-visible) { outline: 2px solid var(--accent); outline-offset: 1px; }

  /* 근거 인라인 확장 — 본문 아래, 가림 없이 답변↔근거 대조 */
  .cite-detail {
    display: flex; align-items: baseline; flex-wrap: wrap; gap: 6px 10px;
    margin: 8px 0 2px; padding: 8px 10px;
    border: 0.5px solid var(--border-strong); border-left: 3px solid var(--accent);
    border-radius: var(--r-md); background: var(--bg-soft); font-size: 12.5px;
  }
  .cd-ref { font-weight: 500; color: var(--accent); }
  .cd-snip { color: var(--text-soft); line-height: 1.6; flex: 1 1 100%; overflow-wrap: anywhere; }
  .cd-link { color: var(--accent); text-decoration: none; font-size: 12px; }
  .cd-link:hover { text-decoration: underline; }
  .cd-close { margin-left: auto; border: none; background: transparent; cursor: pointer; color: var(--text-faint); font-size: 12px; }
  .cd-close:hover { color: var(--text); }
</style>
