"""legal_infra — legal_core Protocol 구현체 (Qdrant·Fuseki·원격 임베딩).

클라이언트 의존(qdrant-client·rdflib·requests)을 이 패키지에 격리한다.
backend·db-admin이 DI로 주입해 사용. (Design v0.3.1 §9.2)
"""

from __future__ import annotations

from legal_infra.embedding_flag import LocalFlagEmbedding
from legal_infra.embedding_remote import RemoteEmbedding
from legal_infra.graph_fuseki import FusekiGraph
from legal_infra.provider import build_embedding, build_retrieval_service
from legal_infra.reranker_flag import LocalFlagReranker
from legal_infra.vector_qdrant import QdrantVector

__all__ = ["RemoteEmbedding", "LocalFlagEmbedding", "LocalFlagReranker",
           "QdrantVector", "FusekiGraph",
           "build_embedding", "build_retrieval_service"]