<script lang="ts">
  import type { TranscriptItem } from '$lib/entities/message/types';
  import type { Citation } from '$lib/shared/api/contracts';
  import Markdown from '$lib/shared/render/Markdown.svelte';

  let {
    items = [] as TranscriptItem[],
    citations = [] as Citation[],
    onfork = (_id: string) => {}
  } = $props();

  // 분기 — 실수 클릭 방지 위해 확인 후 진행(원본은 비파괴).
  function doFork(id: string): void {
    if (confirm('이 질문에서 새 분기 대화를 만들까요?\n원본 대화는 그대로 유지되고, 새 분기로 이동합니다.')) {
      onfork(id);
    }
  }

  // 도구 인자 미리보기 — 값들을 짧게 요약(예: {query:'거실'} → "거실"). O(인자수).
  function argsPreview(args: unknown): string {
    if (!args || typeof args !== 'object') return '';
    const vals = Object.values(args as Record<string, unknown>)
      .filter((v) => v !== '' && v != null)
      .map((v) => (typeof v === 'string' ? v : JSON.stringify(v)));
    const s = vals.join(', ');
    return s.length > 48 ? s.slice(0, 47) + '…' : s;
  }
</script>

<div class="conv">
  {#each items as it (it.id)}
    {#if it.kind === 'tool'}
      <div class="toolrow" title={it.result ?? ''}>
        <span class="ico" aria-hidden="true">🔧</span>
        <span class="tname">{it.name}</span>
        {#if argsPreview(it.args)}<span class="targs">({argsPreview(it.args)})</span>{/if}
        {#if it.status === 'done'}
          <span class="tres">→ {it.result}</span>
        {:else}
          <span class="tres muted">실행 중…</span>
        {/if}
      </div>
    {:else}
      <div class="row {it.role}">
        <div class="avatar">{it.role === 'user' ? '나' : 'AI'}</div>
        <div class="bubble">
          {#if it.role === 'agent'}
            <!-- 에이전트 답변: markdown 렌더(새니타이즈) + 인용 칩 -->
            <Markdown source={it.text} {citations} />
          {:else}
            <!-- 사용자 메시지: 평문 -->
            {it.text}
          {/if}
        </div>
        <!-- 분기: 백엔드 message id 가 있는(이력 로드된) 사용자 메시지에서만 -->
        {#if it.role === 'user' && it.backendId}
          {@const bid = it.backendId}
          <button class="fork" title="여기서 분기 — 이 질문부터 새 대화로 갈라냄(원본 유지)" aria-label="여기서 분기" onclick={() => doFork(bid)}>⑂ 분기</button>
        {/if}
      </div>
    {/if}
  {/each}
</div>

<style>
  .conv { display: flex; flex-direction: column; gap: 14px; padding: 16px; }
  .row { display: flex; gap: 10px; align-items: flex-start; }
  .fork {
    flex: 0 0 auto; align-self: center; opacity: 0; transition: opacity 0.1s; box-sizing: border-box;
    border: 0.5px solid var(--border-strong); background: var(--bg); color: var(--text-faint);
    border-radius: var(--r-sm); padding: 2px 7px; font-size: 13px; cursor: pointer; line-height: 1;
  }
  /* 호버(데스크톱) + 키보드 포커스(접근성)서 노출 */
  .row:hover .fork, .row:focus-within .fork { opacity: 1; }
  .fork:hover { background: var(--bg-soft); color: var(--text-soft); }
  /* 반응형 M6: 모바일은 호버가 없으므로 분기 버튼 상시 노출 + 터치 타깃 ≥44px(P5). */
  @media (max-width: 760px) {
    .fork {
      opacity: 1; min-height: 44px; padding: 0 12px;
      display: inline-flex; align-items: center; font-size: 14px;
    }
    /* 44px fork 가 짧은 1줄 버블 행을 44px 로 키울 때 아바타/버블이 위로 뜨는 정렬 깨짐 방지(M6-#4). */
    .row.user { align-items: center; }
  }
  .avatar {
    flex: 0 0 26px; width: 26px; height: 26px; border-radius: 50%;
    display: flex; align-items: center; justify-content: center;
    font-size: 11px; background: var(--bg-inset); color: var(--text-soft);
  }
  .row.agent .avatar { background: var(--accent-soft); color: var(--accent); }
  .bubble { font-size: 14px; line-height: 1.8; padding-top: 2px; min-width: 0; white-space: normal; overflow-wrap: anywhere; }
  .row.user .bubble { white-space: pre-wrap; }
  .toolrow {
    margin-left: 36px; display: flex; align-items: baseline; gap: 6px;
    font-size: 12px; color: var(--text-soft);
    background: var(--bg-soft); border: 0.5px solid var(--border);
    border-radius: var(--r-md); padding: 5px 9px; width: fit-content; max-width: 100%;
  }
  .toolrow .ico { font-size: 11px; flex: 0 0 auto; }
  .toolrow .tname { color: var(--text); font-weight: 500; flex: 0 0 auto; }
  .toolrow .targs { color: var(--text-soft); flex: 0 0 auto; }
  .toolrow .tres {
    color: var(--text-faint); min-width: 0;
    white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
  }
  .toolrow .muted { color: var(--text-faint); }
</style>
