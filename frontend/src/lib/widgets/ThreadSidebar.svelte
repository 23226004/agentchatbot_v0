<script lang="ts">
  // 스레드 목록 + 새 대화 + **이름변경(인라인)·삭제(호버)**. 프레젠테이션 전용 — 콜백만 emit.
  import type { ThreadSummary } from '$lib/shared/api/history';

  interface Props {
    threads?: ThreadSummary[];
    activeId?: string | null;
    onselect?: (id: string) => void;
    onnew?: () => void;
    onrename?: (id: string, title: string) => void;
    ondelete?: (id: string) => void;
  }
  let {
    threads = [],
    activeId = null,
    onselect = () => {},
    onnew = () => {},
    onrename = () => {},
    ondelete = () => {}
  }: Props = $props();

  // 인라인 이름변경 상태(한 번에 하나).
  let editingId = $state<string | null>(null);
  let editValue = $state('');

  function startRename(t: ThreadSummary): void {
    editingId = t.id;
    editValue = t.title ?? '';
  }
  function commitRename(): void {
    if (editingId === null) return;
    const id = editingId;
    editingId = null; // 먼저 닫아 blur 재진입 무력화(Enter→blur 이중호출 방지)
    onrename(id, editValue);
  }
  function cancelRename(): void {
    editingId = null;
  }
  function onEditKey(e: KeyboardEvent): void {
    if (e.key === 'Enter') {
      e.preventDefault();
      commitRename();
    } else if (e.key === 'Escape') {
      e.preventDefault();
      cancelRename();
    }
  }
  function doDelete(t: ThreadSummary): void {
    if (confirm(`"${(t.title || '새 대화')}" 대화를 삭제할까요? 되돌릴 수 없습니다.`)) {
      ondelete(t.id);
    }
  }
  // 편집 시작 시 입력에 포커스+전체선택.
  function autofocus(node: HTMLInputElement) {
    node.focus();
    node.select();
  }

  // 갱신 시각 → 짧은 상대시간(제목 없는 스레드 구분용). O(1).
  function ago(iso: string): string {
    const t = Date.parse(iso);
    if (Number.isNaN(t)) return '';
    const s = Math.max(0, (Date.now() - t) / 1000);
    if (s < 60) return '방금';
    if (s < 3600) return `${Math.floor(s / 60)}분 전`;
    if (s < 86400) return `${Math.floor(s / 3600)}시간 전`;
    if (s < 7 * 86400) return `${Math.floor(s / 86400)}일 전`;
    const d = new Date(t);
    return `${d.getMonth() + 1}/${d.getDate()}`;
  }
</script>

<aside class="threads">
  <button class="new" onclick={onnew}>+ 새 대화</button>
  <div class="list">
    {#each threads as t (t.id)}
      <div class="item" class:on={activeId === t.id}>
        {#if editingId === t.id}
          <input
            class="t-edit"
            bind:value={editValue}
            use:autofocus
            onkeydown={onEditKey}
            onblur={commitRename}
            maxlength="500"
            placeholder="대화명"
          />
        {:else}
          <button class="t-main" title={(t.title || '새 대화')} onclick={() => onselect(t.id)}>
            <span class="t-title" class:untitled={!t.title}>{(t.title || '새 대화')}</span>
            <span class="t-time">{ago(t.updated_at)}</span>
          </button>
          <div class="actions">
            <button class="act" title="이름 변경" aria-label="이름 변경" onclick={() => startRename(t)}>✎</button>
            <button class="act danger" title="삭제" aria-label="삭제" onclick={() => doDelete(t)}>🗑</button>
          </div>
        {/if}
      </div>
    {/each}
    {#if !threads.length}<p class="empty">대화 기록이 없습니다.</p>{/if}
  </div>
</aside>

<style>
  .threads { display: flex; flex-direction: column; border-right: 0.5px solid var(--border); background: var(--bg); min-height: 0; }
  .new {
    margin: 10px 10px 6px; padding: 7px 10px; font-size: 12.5px; text-align: left;
    border: 0.5px solid var(--border-strong); border-radius: var(--r-md); background: var(--bg); color: var(--text-soft); cursor: pointer;
  }
  .new:hover { background: var(--bg-soft); }
  .list { flex: 1; overflow-y: auto; padding: 0 8px 10px; display: flex; flex-direction: column; gap: 2px; }
  .item {
    display: flex; align-items: center; gap: 2px;
    border-radius: var(--r-md); padding-right: 4px; min-width: 0;
  }
  .item:hover { background: var(--bg-soft); }
  .item.on { background: var(--accent-soft); }
  .item.on .t-title { color: var(--accent); }
  .t-main {
    flex: 1; min-width: 0;
    display: flex; flex-direction: column; gap: 1px;
    text-align: left; border: none; background: transparent; cursor: pointer;
    padding: 6px 9px; border-radius: var(--r-md);
  }
  .t-title {
    font-size: 12.5px; color: var(--text-soft);
    white-space: nowrap; overflow: hidden; text-overflow: ellipsis; max-width: 100%;
  }
  .t-title.untitled { color: var(--text-faint); font-style: italic; }
  .t-time { font-size: 10.5px; color: var(--text-faint); }
  /* 액션: 평소 숨김, 항목 호버/포커스 시 노출(키보드 접근성). */
  .actions { display: flex; gap: 1px; opacity: 0; transition: opacity 0.1s; }
  .item:hover .actions, .item:focus-within .actions { opacity: 1; }
  .act {
    border: none; background: transparent; cursor: pointer;
    font-size: 12px; line-height: 1; padding: 4px 5px; border-radius: var(--r-sm);
    color: var(--text-faint);
  }
  .act:hover { background: var(--bg); color: var(--text-soft); }
  .act.danger:hover { color: #d9534f; }
  .t-edit {
    flex: 1; min-width: 0; margin: 3px 5px;
    font-size: 12.5px; padding: 4px 7px;
    border: 0.5px solid var(--accent); border-radius: var(--r-md);
    background: var(--bg); color: var(--text);
  }
  .t-edit:focus { outline: none; }
  .empty { font-size: 11.5px; color: var(--text-faint); padding: 6px 9px; }
</style>
