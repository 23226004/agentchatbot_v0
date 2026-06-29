// 실행 계획(Task 사이드바 계획 탭) — Codex 벤치마크. 실제 스트림 이벤트에서 동적 합성.
// id 기반: 같은 도구를 여러 번 호출해도 라벨 충돌 없이 개별 스텝으로 추적(O(n)).
// [후속] backend `plan.updated` 이벤트로 대체(FE Plan §4.2).
export type StepStatus = 'pending' | 'active' | 'done';
export interface PlanStep {
  id: string;
  label: string;
  status: StepStatus;
}

let _pseq = 0;

export class PlanStore {
  steps = $state<PlanStep[]>([]);

  /** 스텝을 추가하고 그 id 를 반환(호출부가 id 로 상태 전이). */
  add(label: string, status: StepStatus = 'pending'): string {
    const id = `p${++_pseq}`;
    this.steps.push({ id, label, status });
    return id;
  }

  /** id 로 상태 전이. */
  set(id: string, status: StepStatus): void {
    const s = this.steps.find((x) => x.id === id);
    if (s) s.status = status;
  }

  /** 아직 안 끝난 스텝을 모두 done 으로(정상 완료 시). */
  finishAll(): void {
    for (const s of this.steps) if (s.status !== 'done') s.status = 'done';
  }

  reset(): void {
    this.steps = [];
  }
}
