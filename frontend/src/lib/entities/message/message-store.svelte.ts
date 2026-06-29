// 트랜스크립트 스토어 — runes 기반. append-only + active 셀만 갱신(O(n)).
import type { TranscriptItem } from './types';

let _seq = 0;
const uid = () => String(++_seq);

export class Transcript {
  items = $state<TranscriptItem[]>([]);

  addUser(text: string): void {
    this.items.push({ id: uid(), kind: 'message', role: 'user', text, status: 'committed' });
  }

  startAgent(): string {
    const id = uid();
    this.items.push({ id, kind: 'message', role: 'agent', text: '', status: 'streaming' });
    return id;
  }

  appendDelta(id: string, chunk: string): void {
    const m = this.items.find((i) => i.id === id);
    if (m?.kind === 'message') m.text += chunk;
  }

  commit(id: string, text?: string): void {
    const m = this.items.find((i) => i.id === id);
    if (m?.kind === 'message') {
      if (text != null) m.text = text;
      m.status = 'committed';
    }
  }

  toolCall(name: string, args: unknown): string {
    const id = uid();
    this.items.push({ id, kind: 'tool', name, args, status: 'running' });
    return id;
  }

  toolResult(id: string, content: string): void {
    const t = this.items.find((i) => i.id === id);
    if (t?.kind === 'tool') {
      t.result = content;
      t.status = 'done';
    }
  }

  reset(): void {
    this.items = [];
  }

  // 이력 복원: backend get_thread_messages 행(user/agent/tool)을 트랜스크립트로.
  loadMessages(rows: { id?: string; role: string; content_md: string | null; tool_name: string | null; tool_args?: unknown; tool_result: unknown }[]): void {
    this.items = rows.flatMap<TranscriptItem>((r) => {
      if (r.role === 'tool') {
        const res = r.tool_result == null ? undefined
          : typeof r.tool_result === 'string' ? r.tool_result : JSON.stringify(r.tool_result);
        const cell: TranscriptItem = { id: uid(), kind: 'tool', name: r.tool_name ?? 'tool', args: r.tool_args, result: res, status: 'done' };
        return [cell];
      }
      if (r.role === 'user' || r.role === 'agent') {
        // backendId=백엔드 message UUID(분기 fork_point 용) — 이력 로드 시에만 존재.
        const msg: TranscriptItem = { id: uid(), backendId: r.id, kind: 'message', role: r.role, text: r.content_md ?? '', status: 'committed' };
        return [msg];
      }
      return [];
    });
  }
}
