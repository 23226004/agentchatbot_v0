"""legal_core — 법령 GraphRAG 공유 코어.

backend·agent·db-admin이 의존하는 최하위 레이어 (Design v0.3.1 §9.1).
구현(Fuseki/Qdrant/FlagEmbedding/TEI)은 상위에서 Protocol을 만족하도록 주입한다.
"""

from __future__ import annotations

from legal_core import ids
from legal_core.repositories import (
    EXPAND_PREDICATES_FULL,
    EXPAND_PREDICATES_V1,
    EmbeddingProvider,
    GraphRepository,
    Reranker,
    VectorRepository,
)
from legal_core.schemas import (
    AnswerContext,
    Chunk,
    DenseSparse,
    Hit,
    LawRef,
)

__all__ = [
    "ids",
    "DenseSparse",
    "Chunk",
    "Hit",
    "LawRef",
    "AnswerContext",
    "EmbeddingProvider",
    "VectorRepository",
    "Reranker",
    "GraphRepository",
    "EXPAND_PREDICATES_V1",
    "EXPAND_PREDICATES_FULL",
]
