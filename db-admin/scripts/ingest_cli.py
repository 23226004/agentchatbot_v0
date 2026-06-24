"""법령 현행 본문 적재 CLI (레포 영속 — scratchpad 승격).

사용: python -m scripts.ingest_cli 건축법 [주차장법 ...]
환경변수: LAW_API_OC, QDRANT_URL, FUSEKI_URL, FUSEKI_ADMIN_PASSWORD, EMBEDDING_URL
"""

from __future__ import annotations

import os
import sys

from legal_infra import FusekiGraph, LocalFlagEmbedding, QdrantVector, RemoteEmbedding

from db_admin.lawgo_client import LawGoClient
from db_admin.pipeline.lawgo_ingest import ingest_law


def main(names: list[str]) -> None:
    client = LawGoClient(oc=os.environ.get("LAW_API_OC", "leehm21897"))
    graph = FusekiGraph(os.environ.get("FUSEKI_URL", "http://localhost:3030"),
                        dataset=os.environ.get("FUSEKI_DATASET", "law"),
                        auth=("admin", os.environ.get("FUSEKI_ADMIN_PASSWORD", "admin123")))
    vector = QdrantVector(os.environ.get("QDRANT_URL", "http://localhost:6333"),
                          collection=os.environ.get("QDRANT_COLLECTION", "law_articles"))
    # 검색과 동일 모델이어야 정합 — 기본 FlagEmbedding(dense+sparse)
    emb = (RemoteEmbedding(os.environ.get("EMBEDDING_URL", "http://100.119.61.82:8081/v1"))
           if os.environ.get("EMBEDDING_BACKEND") == "remote" else LocalFlagEmbedding())
    for name in names:
        stat = ingest_law(name, client=client, graph=graph, vector=vector, embedding=emb)
        print(f"[적재] {stat['statute']}({stat['law_id']}) "
              f"조문 {stat['articles']} · 청크 {stat['chunks']} · 트리플 {stat['triples']}")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("usage: python -m scripts.ingest_cli <법령명> [...]"); sys.exit(1)
    main(sys.argv[1:])
