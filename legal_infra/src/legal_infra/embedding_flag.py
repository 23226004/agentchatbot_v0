"""인프로세스 BGE-m3 임베딩 — dense + sparse 동시 (FlagEmbedding).

원격 llama.cpp(dense-only)와 달리 sparse(lexical weights)까지 산출 → 하이브리드.
모델은 첫 사용 시 1회 로드(lazy). EmbeddingProvider Protocol 구현.
"""

from __future__ import annotations

from legal_core.schemas import DenseSparse


class LocalFlagEmbedding:
    def __init__(self, model_name: str = "BAAI/bge-m3", use_fp16: bool = False,
                 batch_size: int = 8) -> None:
        self.model_name = model_name
        self.use_fp16 = use_fp16
        self.batch_size = batch_size
        self._model = None

    @property
    def model(self):
        if self._model is None:
            from FlagEmbedding import BGEM3FlagModel
            self._model = BGEM3FlagModel(self.model_name, use_fp16=self.use_fp16)
        return self._model

    def embed(self, texts: list[str]) -> list[DenseSparse]:
        if not texts:
            return []
        out = self.model.encode(
            texts, batch_size=self.batch_size,
            return_dense=True, return_sparse=True, return_colbert_vecs=False,
        )
        dense = out["dense_vecs"]
        lexical = out["lexical_weights"]
        result: list[DenseSparse] = []
        for d, lw in zip(dense, lexical):
            sparse = {int(tok): float(w) for tok, w in lw.items() if float(w) > 0}
            result.append(DenseSparse(dense=[float(x) for x in d], sparse=sparse))
        return result
