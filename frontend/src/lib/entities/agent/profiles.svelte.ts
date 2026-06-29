// 에이전트 프로파일 스토어 — 레일·헤더·라우팅이 공유하는 단일 소스.
// 지금은 시드 목록. backend GET /agents 가 생기면 load() 로 교체(하드코딩 아님).
import type { AgentProfile } from './types';

const SEED: AgentProfile[] = [
  { id: 'auto', label: '자동 라우팅', abbr: '자동', ready: true },
  { id: 'legal', label: '법률 에이전트', abbr: '법', ready: true }
  // 새 분야 = 여기에 프로파일 1행 추가(또는 backend load()). 예: 건설기준(KCSC) 구현 시.
];

export class AgentStore {
  agents = $state<AgentProfile[]>(SEED);
  activeId = $state<string>('legal');

  get active(): AgentProfile {
    return this.agents.find((a) => a.id === this.activeId) ?? this.agents[0];
  }

  select(id: string): void {
    const a = this.agents.find((x) => x.id === id);
    if (a?.ready) this.activeId = id;
  }

  // backend GET /agents 연결 시 프로파일 목록 주입.
  load(list: AgentProfile[]): void {
    this.agents = list;
    if (!list.some((a) => a.id === this.activeId)) this.activeId = list[0]?.id ?? '';
  }
}
