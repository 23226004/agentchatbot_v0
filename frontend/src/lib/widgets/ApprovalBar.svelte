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
    sheet?: boolean; // 반응형 M4: 모바일(≤760)서 모달 바텀시트로 격상(R10)
  }
  let { detail = '', tools = [], onApprove = () => {}, onReject = () => {}, sheet = false }: Props = $props();

  let barEl = $state<HTMLElement | null>(null);
  let entered = false; // 진입 포커스 1회 래치 — 리사이즈로 sheet 재평가 시 포커스 가로채기 방지(M4-D5)
  let prevFocus: HTMLElement | null = null; // 결정 해소 후 포커스 복귀 대상(M4-D4)

  // 시트(모바일 모달) 모드: Esc=거절(안전 방향 — 어떤 도구도 실행하지 않음).
  // 백드롭(scrim) 탭은 일부러 무반응 — 실수로 승인/거절 결정이 내려지지 않게(보안 게이트).
  $effect(() => {
    if (!sheet) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        e.preventDefault();
        onReject();
      }
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  });

  // 진입 포커스 — sheet 진입 시 단 1회(role=alertdialog). 래치로 sheet/barEl 재평가 시 재포커스 안 함(M4-D5).
  $effect(() => {
    if (sheet && !entered && barEl) {
      entered = true;
      prevFocus = document.activeElement as HTMLElement | null; // 직전 포커스 보관
      barEl.focus();
    }
  });

  // 언마운트(승인/거절 해소) 시 포커스 복귀 — body 유실 방지(WCAG 2.4.3 포커스순서, M4-D4).
  // deps 없음 → 마운트 1회 + 파괴 시 cleanup. 복귀 대상이 살아있고/보이고/비활성 아님일 때만.
  $effect(() => {
    return () => {
      const p = prevFocus;
      if (p && p.isConnected && p.offsetParent !== null && !(p as HTMLButtonElement).disabled) p.focus();
    };
  });

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

{#if sheet}
  <!-- 모달 scrim — 배경 가림(시각). 탭 무반응(실수 결정 방지), 키보드 종결은 Esc=거절. -->
  <div class="scrim" aria-hidden="true"></div>
{/if}
<div
  class="bar"
  class:sheet
  bind:this={barEl}
  tabindex="-1"
  role="alertdialog"
  aria-modal={sheet ? 'true' : undefined}
  aria-label="실행 승인 요청"
  aria-describedby="approval-detail"
>
  <div class="head">
    <span class="ico" aria-hidden="true">!</span>
    <span class="detail" id="approval-detail">{detail || '도구 실행을 승인하시겠습니까?'}</span>
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
  .bar:focus { outline: none; } /* tabindex=-1 진입 포커스 — 시각 아웃라인은 불필요(다이얼로그 컨테이너) */

  /* ── 반응형 M4: 모바일 모달 바텀시트(R10) ── */
  .scrim { position: fixed; inset: 0; z-index: 60; background: rgba(0, 0, 0, 0.42); animation: scrim-in 0.18s ease; }
  .bar.sheet {
    position: fixed; left: 0; right: 0; bottom: 0; z-index: 61;
    border: 0.5px solid var(--border); border-bottom: none;
    border-top-left-radius: 14px; border-top-right-radius: 14px;
    max-height: 70vh; overflow-y: auto; overscroll-behavior: contain;
    padding: 14px max(12px, env(safe-area-inset-right)) max(14px, env(safe-area-inset-bottom)) max(12px, env(safe-area-inset-left));
    box-shadow: 0 -4px 20px rgba(0, 0, 0, 0.18);
    /* scrim 위에서 또렷하게 — 경고 틴트 강화(C-D2) */
    background: color-mix(in srgb, var(--warning) 16%, var(--bg));
    animation: sheet-up 0.22s ease;
  }
  /* 터치 타깃 ≥44px — 체크박스/버튼 확대 */
  .bar.sheet .tools label { min-height: 44px; align-items: center; font-size: 14px; }
  .bar.sheet .tools input { width: 20px; height: 20px; }
  .bar.sheet .detail { font-size: 14px; }
  /* 거절/승인은 시트 하단 sticky — 도구 목록이 길어 스크롤돼도 CTA 항상 노출(보안 게이트, C-D1). */
  .bar.sheet .actions {
    position: sticky; bottom: 0; z-index: 1;
    gap: 10px; margin-top: 4px; padding-top: 10px;
    background: color-mix(in srgb, var(--warning) 16%, var(--bg));
    border-top: 0.5px solid var(--border);
  }
  .bar.sheet .actions button { flex: 1; min-height: 48px; padding: 12px 18px; font-size: 15px; }
  @keyframes scrim-in { from { opacity: 0; } to { opacity: 1; } }
  @keyframes sheet-up { from { transform: translateY(100%); } to { transform: translateY(0); } }
  @media (prefers-reduced-motion: reduce) {
    .bar.sheet { animation: none; }
    .scrim { animation: none; }
  }
</style>
