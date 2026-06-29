// 누적 근거 라이브러리 — citation.added 를 중복제거하며 모은다(FE Plan D6, GET /citations 정합).
import type { Citation } from '$lib/shared/api/contracts';

export class CitationLibrary {
  items = $state<Citation[]>([]);

  add(c: Citation): void {
    if (!this.items.some((x) => x.id === c.id)) this.items.push(c);
  }

  byId(id: string): Citation | undefined {
    return this.items.find((x) => x.id === id);
  }

  clear(): void {
    this.items = [];
  }

  // 이력 복원: distinct citation 목록으로 교체(중복 제거 유지).
  loadMany(list: Citation[]): void {
    this.items = [];
    for (const c of list) this.add(c);
  }
}
