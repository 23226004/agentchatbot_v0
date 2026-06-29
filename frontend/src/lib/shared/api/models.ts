// 모델/설정 REST — GET /models(가용 모델), GET/PUT /settings(model 등). app.py 라우트.
// backend 는 구성된 모든 LLM(GPT 다버전/로컬)을 레지스트리로 등록하고, 모델 선택은 per-run override
// (POST /messages 의 body.model) → settings.model → 기본 순으로 적용한다. FE 는 선택 모델을 메시지마다
// 명시 전송(즉시·검증)하고, PUT /settings 로 기본값을 영속한다.
export interface ModelInfo {
  id: string;
  provider?: string; // "openai" | "compatible"
}

async function jsonOf(res: Response): Promise<Record<string, unknown>> {
  if (!res.ok) throw new Error(`요청 실패 (${res.status})`);
  return (await res.json()) as Record<string, unknown>;
}

export async function getModels(base: string): Promise<ModelInfo[]> {
  const d = await jsonOf(await fetch(`${base}/models`));
  return (d.models as ModelInfo[]) ?? [];
}

export async function getSettingsModel(base: string): Promise<string | null> {
  const d = await jsonOf(await fetch(`${base}/settings`));
  const m = d.model;
  return typeof m === 'string' ? m : null;
}

export async function setSettingsModel(base: string, model: string): Promise<void> {
  const res = await fetch(`${base}/settings`, {
    method: 'PUT',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify({ model })
  });
  if (!res.ok) throw new Error(`설정 저장 실패 (${res.status})`);
}
