"""인프로세스 BGE-reranker-v2-m3 — cross-encoder 재정렬.

transformers 직접 사용(AutoModelForSequenceClassification) — FlagEmbedding의 reranker는
transformers 5.x와 비호환(prepare_for_model 제거)이라 표준 API로 구현. Reranker Protocol.
"""

from __future__ import annotations

from legal_core.schemas import Hit


class LocalFlagReranker:
    def __init__(self, model_name: str = "BAAI/bge-reranker-v2-m3", max_length: int = 512) -> None:
        self.model_name = model_name
        self.max_length = max_length
        self._tok = None
        self._model = None
        self._torch = None

    def _load(self) -> None:
        if self._model is None:
            import torch
            from transformers import AutoModelForSequenceClassification, AutoTokenizer
            self._tok = AutoTokenizer.from_pretrained(self.model_name)
            self._model = AutoModelForSequenceClassification.from_pretrained(self.model_name).eval()
            self._torch = torch

    def rerank(self, query: str, hits: list[Hit], k: int) -> list[Hit]:
        if not hits:
            return []
        self._load()
        texts = [h.payload.get("text", "") for h in hits]
        with self._torch.no_grad():
            inp = self._tok([query] * len(texts), texts, padding=True, truncation=True,
                            max_length=self.max_length, return_tensors="pt")
            logits = self._model(**inp).logits.view(-1).float()
            scores = self._torch.sigmoid(logits).tolist()
        ranked = sorted(zip(hits, scores), key=lambda t: t[1], reverse=True)[:k]
        return [Hit(uri=h.uri, score=float(s), payload=h.payload) for h, s in ranked]
