"""E2E (레포 영속 — scratchpad 승격): 건축법 '거실 정의' GraphRAG → GPT 인용답변.

전제: 건축법 적재됨(db-admin/scripts/ingest_cli.py 건축법), 로컬 Qdrant/Fuseki + 원격 임베딩 가동.
GPT 키/모델은 agent/.env 의 OPENAI_API / GPT_MODEL.
사용: python backend/scripts/e2e_geosil.py
"""

from __future__ import annotations

import json
import os
import re
import sys
import urllib.request

from backend_app.core.container import build_retrieval_service

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
QUERY = "건축법에서 '거실'의 정의가 무엇인가?"


def _load_openai() -> tuple[str, str]:
    cfg = {}
    for ln in open(os.path.join(_ROOT, "agent", ".env"), encoding="utf-8"):
        ln = ln.strip()
        if ln and not ln.startswith("#") and "=" in ln:
            k, _, v = ln.partition("=")
            cfg[k.strip()] = v.strip()
    return cfg["OPENAI_API"], cfg["GPT_MODEL"]


def _gpt(api_key: str, model: str, articles) -> str:
    block = "\n\n".join(
        f"[id:{r.id}] {r.ref} (시행 {r.eff_date})\n{r.article_text or r.snippet}" for r in articles)
    body = {"model": model, "temperature": 0, "messages": [
        {"role": "system", "content": "한국 법령 어시스턴트. 아래 근거 조문만 사용해 답하고, "
         "인용은 본문에 [[cite:<id>]] 토큰으로 표기하라. 근거에 없으면 모른다고 하라."},
        {"role": "user", "content": f"근거 조문:\n{block}\n\n질문: {QUERY}"}]}
    req = urllib.request.Request(
        "https://api.openai.com/v1/chat/completions", data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {api_key}"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.load(r)["choices"][0]["message"]["content"]


def main() -> int:
    svc = build_retrieval_service()
    ctx = svc.retrieve(QUERY, k=5)
    print("검색 top:", [(r.ref, round(r.score, 3)) for r in ctx.articles])
    api_key, model = _load_openai()
    ans = _gpt(api_key, model, ctx.articles)
    print("\n답변:\n" + ans)

    cited = set(re.findall(r"\[\[cite:([0-9a-f-]+)\]\]", ans))
    ctx_ids = {r.id for r in ctx.articles}
    ok = (bool(cited) and cited <= ctx_ids
          and "거실" in ans and "방" in ans
          and "제2조" in ctx.articles[0].ref)
    print("\n검증:", "PASS" if ok else "FAIL",
          f"(cited⊆ctx={cited <= ctx_ids}, top=제2조={'제2조' in ctx.articles[0].ref})")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
