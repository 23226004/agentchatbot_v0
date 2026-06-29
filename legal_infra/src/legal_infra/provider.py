"""DI 프로바이더 — env → concrete 구현체 → legal_core.RetrievalService (Design §9.2).

backend container 와 agent 도구가 공유한다(중복 제거·agent→backend 역의존 방지).
v1: 원격/로컬 임베딩 + Qdrant + Fuseki, reranker 선택. 인프로세스 슬라이스 형태.
"""

from __future__ import annotations

import os

from legal_core.retrieval import RetrievalService

from legal_infra.embedding_flag import LocalFlagEmbedding
from legal_infra.embedding_remote import RemoteEmbedding
from legal_infra.graph_fuseki import FusekiGraph
from legal_infra.reranker_flag import LocalFlagReranker
from legal_infra.vector_qdrant import QdrantVector


def _need_flag(factory, what: str):
    """LocalFlag* 조립을 시도하되 torch/FlagEmbedding 미설치면 **actionable** 에러로 바꾼다.

    경량 런타임(agent .venv 등)엔 torch 가 없어 기본값(flag)이 첫 호출 때 모호한 ImportError 로
    죽었다(교차검증). 어떤 env 를 바꾸면 되는지 알려준다(remote 전환 또는 legal_infra[flag] 설치).
    """
    try:
        return factory()
    except ImportError as exc:  # FlagEmbedding/torch 부재
        raise RuntimeError(
            f"{what} 로컬 백엔드(FlagEmbedding/torch)가 설치돼 있지 않습니다. "
            f"원격 임베딩 서버를 쓰려면 EMBEDDING_BACKEND=remote(+RERANK=0), "
            f"로컬을 쓰려면 'uv pip install -e legal_infra'(torch 포함) 하세요."
        ) from exc


def build_embedding():
    """EMBEDDING_BACKEND=flag(기본, dense+sparse) | remote(dense-only)."""
    if os.environ.get("EMBEDDING_BACKEND", "flag") == "remote":
        return RemoteEmbedding(
            os.environ.get("EMBEDDING_URL", "http://100.119.61.82:8081/v1"),
            model=os.environ.get("EMBEDDING_MODEL", "bge-m3"),
        )
    return _need_flag(LocalFlagEmbedding, "임베딩")


def build_retrieval_service() -> RetrievalService:
    """env 로 구현체를 골라 RetrievalService 를 조립한다."""
    vec = QdrantVector(
        os.environ.get("QDRANT_URL", "http://localhost:6333"),
        collection=os.environ.get("QDRANT_COLLECTION", "law_articles"),
    )
    graph = FusekiGraph(
        os.environ.get("FUSEKI_URL", "http://localhost:3030"),
        dataset=os.environ.get("FUSEKI_DATASET", "law"),
        auth=("admin", os.environ.get("FUSEKI_ADMIN_PASSWORD", "admin123")),
    )
    reranker = None if os.environ.get("RERANK", "1") == "0" else _need_flag(LocalFlagReranker, "리랭커")
    return RetrievalService(
        embedding=build_embedding(), vector=vec, graph=graph, reranker=reranker
    )
