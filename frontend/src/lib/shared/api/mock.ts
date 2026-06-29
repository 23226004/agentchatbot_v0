// Mock 백엔드 — 실제 RunService(conversation-store §5) SSE 시퀀스를 흉내.
// 기본: run.started → tool.call → citation×N → message.completed → run.done (토큰 delta 없음).
// 데모: 메시지에 위험/승인 키워드가 있으면 approval.requested 에서 일시정지(HITL) → approve() 로 재개.
import type { AgentClient } from './client';
import type { AgentEvent } from './contracts';

const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));
const RUN = 'mock-run';

const FINAL_MD = [
  '기준 시행일자: 2025-06-01',
  '',
  '금전채무를 지연한 경우 민법상 법정이율은 연 5%입니다 [[cite:c-379]]. ' +
    '보증금 1,000만원을 6개월 지연했다면 1,000만원 × 5% × 6/12 = 250,000원이 지연이자입니다. ' +
    '소 제기 이후에는 소송촉진법상 가산이율이 적용될 수 있습니다 [[cite:c-sok3]].',
  '',
  '⚠️ 본 답변은 참고용이며 법률 자문이 아닙니다. 정확한 판단은 전문가 확인이 필요합니다.'
].join('\n');

export function createMockClient(): AgentClient {
  let seq = 0;
  let thread: string | null = 'mock-thread'; // mock 은 스레드 영속이 없어 표식만
  const n = () => ++seq;
  let cancelled = false; // 중지(interrupt) 플래그 — 각 단계서 확인해 협조취소.

  async function* finish(): AsyncIterable<AgentEvent> {
    if (cancelled) { yield { type: 'run.done', run_id: RUN, seq: n() }; return; }
    yield { type: 'tool.result', id: 't1', content: '민법 §379, 소송촉진법 §3 (2건)', seq: n() };
    await sleep(250);
    if (cancelled) { yield { type: 'run.done', run_id: RUN, seq: n() }; return; }
    yield { type: 'citation.added', seq: n(), id: 'c-379', kind: '법령', title: '민법 제379조', ref: '민법 §379', snippet: '법정이율 — 연 5%', url: 'https://www.law.go.kr' };
    yield { type: 'citation.added', seq: n(), id: 'c-sok3', kind: '법령', title: '소송촉진 등에 관한 특례법 제3조', ref: '소촉법 §3', snippet: '소 제기 후 가산이율', url: 'https://www.law.go.kr' };
    await sleep(300);
    yield { type: 'message.completed', seq: n(), text: FINAL_MD, content_type: 'markdown', citations: ['c-379', 'c-sok3'] };
    yield { type: 'run.done', run_id: RUN, seq: n() };
  }

  return {
    async *send(text: string, _model?: string | null): AsyncIterable<AgentEvent> {
      void _model; // mock 은 단일 모델 — 선택값은 무시(실 backend 가 per-run override 적용)
      seq = 0; // 새 대화 턴
      cancelled = false;
      yield { type: 'run.started', run_id: RUN, thread_id: 'default', seq: n() };
      await sleep(250);
      if (cancelled) { yield { type: 'run.done', run_id: RUN, seq: n() }; return; }
      yield { type: 'tool.call', id: 't1', name: 'search_legal', args: { query: text.slice(0, 60) }, seq: n() };
      await sleep(400);
      if (cancelled) { yield { type: 'run.done', run_id: RUN, seq: n() }; return; }
      if (/삭제|위험|승인|approve|중요|전송/i.test(text)) {
        // 위험·민감 동작 → 승인 게이트(HITL). 일시정지(run.done 없이 종료).
        yield {
          type: 'approval.requested', seq: n(),
          id: RUN, run_id: RUN, action: 'tool', detail: 'search_legal 도구 실행을 승인하시겠습니까?',
          tools: [{ id: 't1', name: 'search_legal', args: { query: text.slice(0, 60) } }]
        };
        return;
      }
      yield* finish();
    },

    async *approve(approve: boolean, _approved?: string[]): AsyncIterable<AgentEvent> {
      void _approved; // mock 은 단일 도구 — 선택 목록 무시(실 backend 가 선택 실행 적용)
      cancelled = false;
      if (!approve) {
        yield { type: 'run.done', run_id: RUN, seq: n() };
        return;
      }
      await sleep(200);
      yield* finish();
    },

    setThread(id: string | null): void {
      thread = id ?? 'mock-thread';
    },
    currentThread(): string | null {
      return thread;
    },
    async interrupt(): Promise<void> {
      cancelled = true; // 다음 단계서 협조취소 → run.done 으로 종결
    }
  };
}
