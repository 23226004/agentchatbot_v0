"""DI 컨테이너 — env로 구현체 선택·주입 (Design §9.2).

v1: 원격 임베딩(dense) + Qdrant + Fuseki, reranker 없음.
"""

from __future__ import annotations

import os

from legal_infra import (
    FusekiGraph,
    LocalFlagEmbedding,
    LocalFlagReranker,
    QdrantVector,
    RemoteEmbedding,
)

from backend_app.services.retrieval import RetrievalService


def build_embedding():
    """EMBEDDING_BACKEND=flag(기본, dense+sparse) | remote(dense-only)."""
    if os.environ.get("EMBEDDING_BACKEND", "flag") == "remote":
        return RemoteEmbedding(os.environ.get("EMBEDDING_URL", "http://100.119.61.82:8081/v1"),
                               model=os.environ.get("EMBEDDING_MODEL", "bge-m3"))
    return LocalFlagEmbedding()


def build_retrieval_service() -> RetrievalService:
    vec = QdrantVector(
        os.environ.get("QDRANT_URL", "http://localhost:6333"),
        collection=os.environ.get("QDRANT_COLLECTION", "law_articles"),
    )
    graph = FusekiGraph(
        os.environ.get("FUSEKI_URL", "http://localhost:3030"),
        dataset=os.environ.get("FUSEKI_DATASET", "law"),
        auth=("admin", os.environ.get("FUSEKI_ADMIN_PASSWORD", "admin123")),
    )
    reranker = None if os.environ.get("RERANK", "1") == "0" else LocalFlagReranker()
    return RetrievalService(embedding=build_embedding(), vector=vec, graph=graph, reranker=reranker)
