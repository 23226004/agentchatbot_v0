"""라이브 인프라 계약 테스트 (Qdrant/Fuseki 가동 시만; 미가동 시 skip).

페이크가 못 잡는 실연동 계약을 검증: named graph UNION 매칭, named vector using=DENSE,
payload 필터 적용. Design §11 Integration.
"""

from __future__ import annotations

import socket

import pytest
import requests

from legal_core import ids
from legal_core.schemas import Chunk, DenseSparse
from legal_infra import FusekiGraph, QdrantVector

_LO = "https://2026agent.kr/ontology#delegatesTo"


def _up(host: str, port: int) -> bool:
    try:
        socket.create_connection((host, port), timeout=1).close()
        return True
    except OSError:
        return False


QDR_UP = _up("localhost", 6333)
FUS_UP = _up("localhost", 3030)


@pytest.mark.skipif(not FUS_UP, reason="Fuseki localhost:3030 미가동")
def test_fuseki_add_nt_then_expand_named_graph(tmp_path):
    """add_nt(named graph) → expand가 default+named UNION으로 매칭하는가."""
    g = FusekiGraph("http://localhost:3030", dataset="law", auth=("admin", "admin123"))
    gu = "https://2026agent.kr/law/graph/__it__"
    child, parent = ids.resource_iri("ITCHILD"), ids.resource_iri("ITPARENT")
    nt = tmp_path / "t.nt"
    nt.write_text(f"<{child}> <{_LO}> <{parent}> .\n", encoding="utf-8")
    try:
        assert g.add_nt(str(nt), graph_uri=gu) == 1
        edge = (child, _LO, parent)
        # 양방향: child(outgoing)·parent(incoming) 모두 같은 엣지를 회수
        assert edge in g.expand([child], predicates=["lo:delegatesTo"], depth=1, limit=10)
        assert edge in g.expand([parent], predicates=["lo:delegatesTo"], depth=1, limit=10)  # 하위 조회
    finally:
        requests.post("http://localhost:3030/law/update",
                      data={"update": f"DROP GRAPH <{gu}>"}, auth=("admin", "admin123"))


@pytest.mark.skipif(not QDR_UP, reason="Qdrant localhost:6333 미가동")
def test_qdrant_upsert_search_with_filter():
    """ensure_collection→upsert→search(using=DENSE) + payload 필터 적용 검증."""
    v = QdrantVector("http://localhost:6333", collection="__it_law_articles__", dim=4)
    v.ensure_collection()
    try:
        uri = ids.article_iri("ITLAW", "20260101", 2)
        ch = Chunk(uri=uri, resource_id="ITLAW", statute="테스트법", article_no="제2조",
                   article_key="k", eff_date="2026-01-01", ministry="x", text="거실", is_current=True)
        v.upsert([ch], [DenseSparse(dense=[1.0, 0.0, 0.0, 0.0], sparse={})])
        hits = v.search(DenseSparse(dense=[1.0, 0.0, 0.0, 0.0], sparse={}), k=3, flt={"is_current": True})
        assert hits and hits[0].payload["uri"] == uri          # dense 검색 + 현행 필터 통과
        excluded = v.search(DenseSparse(dense=[1.0, 0.0, 0.0, 0.0], sparse={}), k=3, flt={"is_current": False})
        assert all(h.payload["uri"] != uri for h in excluded)  # 필터 실제 배제
    finally:
        v.client.delete_collection("__it_law_articles__")


@pytest.mark.skipif(not QDR_UP, reason="Qdrant localhost:6333 미가동")
def test_qdrant_hybrid_rrf_live():
    """라이브 하이브리드: sparse 슬롯 upsert + dense/sparse prefetch RRF 검색(v4 간판경로)."""
    v = QdrantVector("http://localhost:6333", collection="__it_hybrid__", dim=4)
    v.ensure_collection()
    try:
        uri = ids.article_iri("ITH", "20260101", 2)
        ch = Chunk(uri=uri, resource_id="ITH", statute="t", article_no="제2조", article_key="k",
                   eff_date="2026-01-01", ministry="x", text="거실", is_current=True, seq=0)
        v.upsert([ch], [DenseSparse(dense=[1.0, 0.0, 0.0, 0.0], sparse={3: 0.9, 7: 0.5})])
        # sparse 있는 쿼리 → 하이브리드 RRF 경로 실행(서버가 prefetch+fusion 수용하는지)
        hits = v.search(DenseSparse(dense=[1.0, 0.0, 0.0, 0.0], sparse={3: 0.9}), k=3)
        assert hits and hits[0].payload["uri"] == uri
    finally:
        v.client.delete_collection("__it_hybrid__")
