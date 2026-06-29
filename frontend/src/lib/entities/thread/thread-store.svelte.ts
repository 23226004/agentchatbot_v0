// 스레드 목록 스토어(사이드바). backend GET /threads 로 로드.
import type { ThreadSummary } from '$lib/shared/api/history';

export class ThreadStore {
  threads = $state<ThreadSummary[]>([]);
  activeId = $state<string | null>(null);

  load(list: ThreadSummary[]): void {
    this.threads = list;
  }

  setActive(id: string | null): void {
    this.activeId = id;
  }

  // 새로 만든 스레드를 맨 위에 올리고 활성화(중복 제거).
  prepend(t: ThreadSummary): void {
    this.threads = [t, ...this.threads.filter((x) => x.id !== t.id)];
    this.activeId = t.id;
  }

  // 제목 변경 반영(목록 즉시 갱신).
  rename(id: string, title: string): void {
    this.threads = this.threads.map((t) => (t.id === id ? { ...t, title } : t));
  }

  // 삭제 반영 — 목록에서 제거. 활성 스레드였으면 활성 해제.
  remove(id: string): void {
    this.threads = this.threads.filter((t) => t.id !== id);
    if (this.activeId === id) this.activeId = null;
  }
}
