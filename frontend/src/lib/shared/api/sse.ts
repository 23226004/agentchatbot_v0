// 실제 backend SSE 클라이언트 — AgentClient 구현. 실제 라우트(backend/src/backend_app/api/app.py):
//   POST /threads → {id}                              (스레드 1회 생성, UUID)
//   POST /threads/{id}/messages {message} → {run_id}  (백그라운드 run; 동시 run 409)
//   GET  /runs/{run_id}/stream                         (SSE replay+live, terminal=run.done/error)
//   POST /threads/{id}/approve {approve} → {run_id}    (HITL: 같은 run_id 재개/거절)
//
// seq 는 thread 단조(repo.next_seq) → 클라이언트 전역 maxSeq 로 dedup: approve 재스트림이
// 버퍼를 replay 해도 이미 적용한(seq ≤ maxSeq) 이벤트는 건너뛴다.
// [후속] Last-Event-ID 자동 재연결은 backend 후속.
import type { AgentClient } from './client';
import { SSE_EVENT_NAMES, type AgentEvent } from './contracts';

export function createSseClient(baseUrl: string): AgentClient {
  let threadId: string | null = null;
  let activeRunId: string | null = null; // 진행 중 run(중지용). 스트림 종료 시 해제.
  let maxSeq = -1; // thread 단조 seq 전역 dedup

  async function ensureThread(): Promise<string> {
    if (threadId) return threadId;
    const res = await fetch(`${baseUrl}/threads`, {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: '{}'
    });
    if (!res.ok) throw new Error(`스레드 생성 실패 (${res.status})`);
    threadId = ((await res.json()) as { id: string }).id;
    return threadId;
  }

  async function* streamRun(runId: string): AsyncIterable<AgentEvent> {
    const es = new EventSource(`${baseUrl}/runs/${encodeURIComponent(runId)}/stream`);
    const queue: AgentEvent[] = [];
    let wake: (() => void) | null = null;
    let done = false;
    let terminated = false;

    const push = (ev: AgentEvent) => {
      queue.push(ev);
      wake?.();
      wake = null;
    };

    for (const name of SSE_EVENT_NAMES) {
      es.addEventListener(name, (e) => {
        const data = JSON.parse((e as MessageEvent).data) as { seq?: number };
        if (typeof data.seq === 'number') {
          if (data.seq <= maxSeq) return; // replay·중복 dedup(thread 단조)
          maxSeq = data.seq;
        }
        // message.completed 이후 끊김은 답변이 이미 도착했으므로 가짜 error 를 만들지 않는다
        // (terminated 로 onerror 침묵). 스트림 종료는 run.done/error 에서만.
        if (name === 'message.completed' || name === 'run.done' || name === 'error') terminated = true;
        push({ type: name, ...data } as AgentEvent);
        if (name === 'run.done' || name === 'error') {
          done = true;
          es.close();
        }
      });
    }
    es.onerror = () => {
      if (!terminated) push({ type: 'error', message: '연결이 끊겼습니다.', seq: maxSeq + 1 });
      done = true;
      es.close();
      wake?.();
      wake = null;
    };

    try {
      while (!done || queue.length) {
        if (!queue.length) await new Promise<void>((r) => (wake = r));
        while (queue.length) yield queue.shift()!;
      }
    } finally {
      es.close();
    }
  }

  async function* postAndStream(path: string, body: unknown): AsyncIterable<AgentEvent> {
    let runId: string;
    try {
      const res = await fetch(`${baseUrl}${path}`, {
        method: 'POST',
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify(body)
      });
      if (!res.ok) {
        throw new Error(res.status === 409 ? '이미 실행 중인 작업이 있습니다.' : `요청 실패 (${res.status})`);
      }
      runId = ((await res.json()) as { run_id: string }).run_id;
      activeRunId = runId; // 중지(interrupt) 대상
    } catch (e) {
      yield { type: 'error', message: (e as Error)?.message ?? '요청에 실패했습니다.', seq: maxSeq + 1 };
      return;
    }
    try {
      yield* streamRun(runId);
    } finally {
      if (activeRunId === runId) activeRunId = null; // 종료 시 해제(중지 대상 아님)
    }
  }

  return {
    async *send(text: string, model?: string | null): AsyncIterable<AgentEvent> {
      let tid: string;
      try {
        tid = await ensureThread();
      } catch (e) {
        yield { type: 'error', message: (e as Error)?.message ?? '요청에 실패했습니다.', seq: maxSeq + 1 };
        return;
      }
      // model 명시 시 backend 가 그 run 만 해당 모델로 실행(per-run override). 미명시면 저장된 settings·기본.
      const body = model ? { message: text, model } : { message: text };
      yield* postAndStream(`/threads/${tid}/messages`, body);
    },

    async *approve(approve: boolean, approved?: string[]): AsyncIterable<AgentEvent> {
      if (!threadId) {
        yield { type: 'error', message: '대화 스레드가 없습니다.', seq: maxSeq + 1 };
        return;
      }
      // approved 지정 시 선택 실행(그 도구만), 미지정이면 전체 승인/거절.
      const body = approved ? { approve, approved } : { approve };
      yield* postAndStream(`/threads/${threadId}/approve`, body);
    },

    setThread(id: string | null): void {
      if (id !== threadId) maxSeq = -1; // 스레드별 seq 공간 → 전환 시 dedup 리셋
      threadId = id;
    },
    currentThread(): string | null {
      return threadId;
    },
    async interrupt(): Promise<void> {
      const rid = activeRunId;
      if (!rid) return; // 활성 run 없음 → no-op
      // 협조취소: backend 가 다음 청크서 종결 → 진행 중 streamRun 이 terminal(run.done/error) 받고 끝남.
      try {
        await fetch(`${baseUrl}/runs/${encodeURIComponent(rid)}/interrupt`, { method: 'POST' });
      } catch {
        /* 중지 요청 실패는 조용히 — 스트림이 알아서 종결하거나 사용자 재시도 */
      }
    }
  };
}
