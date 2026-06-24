"""원격 BGE-m3 임베딩 클라이언트 (llama.cpp `/v1/embeddings`, dense only).

v1: dense 1024만 (sparse는 후속 FlagEmbedding). `EmbeddingProvider` 구현.
"""

from __future__ import annotations

import requests

from legal_core.schemas import DenseSparse


class RemoteEmbedding:
    """OpenAI 호환 임베딩 엔드포인트(원격 8081). EmbeddingProvider Protocol 만족."""

    def __init__(self, base_url: str, model: str = "bge-m3",
                 timeout: float = 60.0, char_budget: int = 4000) -> None:
        self.url = base_url.rstrip("/") + "/embeddings"
        self.model = model
        self.timeout = timeout
        # 한 요청 총 글자수 상한 — 서버 n_ctx(8192토큰) 내로 묶기 위함.
        self.char_budget = char_budget

    def embed(self, texts: list[str]) -> list[DenseSparse]:
        """글자 예산 기반 배치 호출(요청 총 토큰을 n_ctx 이내로). 단일 항목이 예산보다 커도 1건씩 보냄(분할로 ≤max_chars 가정)."""
        out: list[DenseSparse] = []
        batch: list[str] = []
        acc = 0
        for t in texts:
            if batch and acc + len(t) > self.char_budget:
                out.extend(self._embed_batch(batch))
                batch, acc = [], 0
            batch.append(t)
            acc += len(t)
        if batch:
            out.extend(self._embed_batch(batch))
        return out

    def _embed_batch(self, batch: list[str]) -> list[DenseSparse]:
        if not batch:
            return []
        resp = requests.post(
            self.url, json={"input": batch, "model": self.model}, timeout=self.timeout,
        )
        resp.raise_for_status()
        data = resp.json()["data"]
        data.sort(key=lambda d: d.get("index", 0))
        if len(data) != len(batch):
            raise RuntimeError(f"임베딩 개수 불일치: in={len(batch)} out={len(data)}")
        # sparse 는 dense-only 서버라 빈 dict (하이브리드는 후속)
        return [DenseSparse(dense=d["embedding"], sparse={}) for d in data]
