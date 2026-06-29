// 트랜스크립트 항목 — 메시지 또는 도구 셀(append-only).
export type Role = 'user' | 'agent';
export type MessageStatus = 'streaming' | 'committed';

export interface Message {
  id: string; // FE 로컬 id(렌더 키)
  backendId?: string; // 백엔드 message UUID — 이력 로드 시에만 존재(분기 fork_point 용)
  kind: 'message';
  role: Role;
  text: string;
  status: MessageStatus;
}

export interface ToolCell {
  id: string;
  kind: 'tool';
  name: string;
  args: unknown; // LangGraph tool_calls args(객체). 현재 UI 미표시.
  result?: string;
  status: 'running' | 'done';
}

export type TranscriptItem = Message | ToolCell;
