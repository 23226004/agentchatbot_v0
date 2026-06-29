// 이력/스레드 REST — 실제 라우트(app.py): GET /threads, GET /threads/{id}/messages,
// GET /threads/{id}/citations, POST /threads. 스트리밍과 별개(SoC: 같은 shared/api).
import type { Citation } from './contracts';

export interface ThreadSummary {
  id: string;
  title: string | null;
  updated_at: string;
  forked_from_thread_id?: string | null;
}

// get_thread_messages 행(repo): role=user/agent/tool. tool 행은 도구셀.
export interface MessageRow {
  id: string;
  seq: number;
  role: 'user' | 'agent' | 'tool';
  content_md: string | null;
  tool_name: string | null;
  tool_args: unknown;
  tool_result: unknown;
}

async function jsonOf(res: Response): Promise<Record<string, unknown>> {
  if (!res.ok) throw new Error(`요청 실패 (${res.status})`);
  return (await res.json()) as Record<string, unknown>;
}

export async function listThreads(base: string): Promise<ThreadSummary[]> {
  const d = await jsonOf(await fetch(`${base}/threads`));
  return (d.threads as ThreadSummary[]) ?? [];
}

export async function createThread(base: string): Promise<string> {
  const res = await fetch(`${base}/threads`, {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: '{}'
  });
  if (!res.ok) throw new Error(`스레드 생성 실패 (${res.status})`);
  return ((await res.json()) as { id: string }).id;
}

export async function getMessages(base: string, threadId: string): Promise<MessageRow[]> {
  const d = await jsonOf(await fetch(`${base}/threads/${threadId}/messages`));
  return (d.messages as MessageRow[]) ?? [];
}

export async function getCitations(base: string, threadId: string): Promise<Citation[]> {
  const d = await jsonOf(await fetch(`${base}/threads/${threadId}/citations`));
  return (d.citations as Citation[]) ?? [];
}

export interface Summary {
  id: string;
  covers_from_seq: number | null;
  covers_to_seq: number | null;
  content_md: string;
  created_at: string;
}

// 분기 — fork_point 메시지(백엔드 UUID)에서 새 thread 생성(checkpoint state 시드). 새 thread_id 반환.
export async function forkThread(base: string, id: string, forkPointMessageId: string): Promise<string> {
  const res = await fetch(`${base}/threads/${id}/fork`, {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify({ fork_point_message_id: forkPointMessageId })
  });
  if (!res.ok) throw new Error(`분기 실패 (${res.status})`);
  return ((await res.json()) as { thread_id: string }).thread_id;
}

// 범위 대화 LLM 요약. from/to 미지정=전체. 반환은 무시(getSummaries 로 갱신).
export async function summarizeThread(base: string, id: string): Promise<void> {
  const res = await fetch(`${base}/threads/${id}/summarize`, {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: '{}'
  });
  if (!res.ok) {
    throw new Error(res.status === 422 ? '요약할 대화가 없습니다.' : '요약을 생성하지 못했습니다.');
  }
}

export async function getSummaries(base: string, id: string): Promise<Summary[]> {
  const d = await jsonOf(await fetch(`${base}/threads/${id}/summaries`));
  return (d.summaries as Summary[]) ?? [];
}

export async function renameThread(base: string, id: string, title: string): Promise<void> {
  const res = await fetch(`${base}/threads/${id}`, {
    method: 'PATCH',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify({ title })
  });
  if (!res.ok) throw new Error(`이름 변경 실패 (${res.status})`);
}

export async function deleteThread(base: string, id: string): Promise<void> {
  const res = await fetch(`${base}/threads/${id}`, { method: 'DELETE' });
  // 409 = 실행 중(running) — 사용자에게 안내. 그 외 실패도 메시지로.
  if (!res.ok) {
    throw new Error(res.status === 409 ? '실행 중인 대화는 삭제할 수 없습니다.' : `삭제 실패 (${res.status})`);
  }
}
