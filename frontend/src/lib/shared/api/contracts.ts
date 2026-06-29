// FE↔BE 이벤트 계약 — 단일 소스.
// 권위: docs/02-design/features/conversation-store.design.md §5 (RunService SSE 8종, 전부 seq 포함).
// 주의: updates 모드라 message.delta 없음 → 본문은 message.completed 로 전문 도착.

export interface Citation {
  id: string;
  kind: string;
  title: string;
  ref: string;
  snippet?: string;
  url?: string;
}

export type AgentEvent =
  | { type: 'run.started'; run_id: string; thread_id: string; model?: string; seq: number }
  // tool.call args = LangGraph tool_calls 의 args(객체). run_service.py:139 call.get("args").
  | { type: 'tool.call'; id: string; name: string; args: Record<string, unknown>; seq: number }
  | { type: 'tool.result'; id: string; content: string; seq: number }
  | ({ type: 'citation.added'; seq: number } & Citation)
  // 실제 run_service.py 는 {id, run_id, action:"tool", tools:[{id,name,args}], detail} 방출(§5). 필드 optional 로 견고.
  // tools = 대기 중인 도구 호출(per-tool 선택 승인용). 없으면(구버전) 전체 단위 승인.
  | {
      type: 'approval.requested';
      run_id?: string;
      id?: string;
      action?: string;
      detail: string;
      tools?: Array<{ id: string; name: string; args?: Record<string, unknown> }>;
      seq: number;
    }
  | { type: 'message.completed'; text: string; content_type: 'markdown'; citations: string[]; seq: number }
  // error: 일반 {run_id, message} | cite_forgery {run_id, reason, forged, valid}. (terminal — run.done 없음)
  | { type: 'error'; run_id?: string; message?: string; reason?: string; forged?: string[]; valid?: string[]; seq: number }
  | { type: 'run.done'; run_id: string; seq: number };

export const SSE_EVENT_NAMES = [
  'run.started',
  'tool.call',
  'tool.result',
  'citation.added',
  'approval.requested',
  'message.completed',
  'error',
  'run.done'
] as const;
