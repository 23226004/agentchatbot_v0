<script lang="ts">
  let {
    onsend = (_text: string) => {},
    busy = false,
    stoppable = false,
    onstop = () => {}
  } = $props();
  let value = $state('');

  function submit() {
    const text = value.trim();
    if (!text || busy) return;
    onsend(text);
    value = '';
  }

  function onkeydown(e: KeyboardEvent) {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      submit();
    }
  }
</script>

<div class="composer">
  <textarea
    bind:value
    {onkeydown}
    rows="1"
    placeholder="질문을 입력하세요  (  /  로 명령 · ⏎ 전송 )"
  ></textarea>
  {#if stoppable}
    <button class="stop" onclick={() => onstop()} aria-label="중지" title="생성 중지">■</button>
  {:else}
    <button onclick={submit} disabled={busy} aria-label="전송">{busy ? '…' : '↑'}</button>
  {/if}
</div>

<style>
  .composer {
    display: flex; gap: 8px; align-items: flex-end;
    padding: 10px 12px; border-top: 0.5px solid var(--border); background: var(--bg);
  }
  textarea {
    flex: 1; resize: none; border: 0.5px solid var(--border-strong);
    border-radius: var(--r-md); padding: 8px 10px; font: inherit; font-size: 13px;
    background: var(--bg); color: var(--text); max-height: 140px;
  }
  textarea:focus { outline: none; border-color: var(--accent); }
  button {
    flex: 0 0 auto; width: 34px; height: 34px; border-radius: var(--r-md);
    border: 0.5px solid var(--border-strong); background: var(--bg);
    color: var(--text); cursor: pointer; font-size: 16px;
  }
  button:hover:not(:disabled) { background: var(--bg-soft); }
  button:disabled { opacity: 0.5; cursor: default; }
  button.stop { color: #d9534f; border-color: #d9534f; font-size: 12px; }
  button.stop:hover { background: #d9534f; color: #fff; }
</style>
