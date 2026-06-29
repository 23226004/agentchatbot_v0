// 모델 셀렉터 스토어 — 헤더에서 로컬↔GPT 전환. 목록은 backend GET /models 로 로드.
import type { ModelInfo } from '$lib/shared/api/models';

export class ModelStore {
  models = $state<ModelInfo[]>([]);
  activeId = $state<string>('');

  load(list: ModelInfo[], active?: string | null): void {
    this.models = list;
    this.activeId = (active && list.some((m) => m.id === active)) ? active : (list[0]?.id ?? '');
  }

  setActive(id: string): void {
    if (this.models.some((m) => m.id === id)) this.activeId = id;
  }

  get active(): ModelInfo | undefined {
    return this.models.find((m) => m.id === this.activeId);
  }
}
