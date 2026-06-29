// 에이전트 프로파일 로드 — GET /agents (conversation-store §7 / agent-platform).
// backend 가 프로파일을 추가하면 FE 수정 없이 자동 반영된다.
import type { AgentProfile } from '$lib/entities/agent/types';

export async function fetchAgents(base: string): Promise<AgentProfile[]> {
  const res = await fetch(`${base}/agents`);
  if (!res.ok) throw new Error(`agents ${res.status}`);
  const raw = (await res.json()) as Array<Record<string, unknown>>;
  return raw.map((a) => {
    const label = String(a.label ?? a.name ?? a.id ?? '');
    return {
      id: String(a.id ?? ''),
      label,
      abbr: String(a.abbr ?? label.slice(0, 2)), // 미제공 시 라벨 앞글자로 자동 생성
      accent: a.accent ? String(a.accent) : undefined,
      ready: a.ready !== false
    } satisfies AgentProfile;
  });
}
