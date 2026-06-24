"""위임그래프 빌드 (Design FR-02, 슬라이스7).

소스 = law.go.kr **lsStmd(법령체계도)** API. (AI-Hub 상위법령은 2018 부분집합이라
건축법 등 미커버 → "본문=law.go.kr API" 피벗과 일관되게 lsStmd 채택.)
base 법령의 하위 법령(시행령/시행규칙)을 resolve해 `lo:delegatesTo(child→base)` RDF 적재.
검증 게이트: 하위 법령ID는 lsStmd가 권위적으로 제공(이름매칭 모호 없음). 적재 리포트 산출.
"""

from __future__ import annotations

import os
import tempfile

from rdflib import Graph, Namespace, URIRef

from legal_core import ids
from legal_core.lawgo import parse_hierarchy

from db_admin.lawgo_client import LawGoClient

LO = Namespace("https://2026agent.kr/ontology#")
_DELEG_GRAPH = "https://2026agent.kr/law/graph/delegation"


def build_delegation(base_name: str, *, client: LawGoClient, graph) -> dict:
    """base 법령의 위임 하위(delegatesTo child→base)를 적재. 반환: 리포트."""
    found = client.resolve_current(base_name)
    if not found:
        raise RuntimeError(f"'{base_name}' 현행 유일해소 실패 — 위임 빌드 중단")
    base_id, mst = found["법령ID"], found["법령일련번호"]

    children = parse_hierarchy(client.fetch_hierarchy(mst), base_name, base_id)

    g = Graph()
    base_res = URIRef(ids.resource_iri(base_id))
    for cid, _cname in children:
        g.add((URIRef(ids.resource_iri(cid)), LO.delegatesTo, base_res))

    # 멱등: base별 named graph 분리 → 교체적재
    graph_uri = f"{_DELEG_GRAPH}/{base_id}"
    edges = len(children)
    if edges:
        with tempfile.NamedTemporaryFile("w", suffix=".nt", delete=False, encoding="utf-8") as tf:
            tf.write(g.serialize(format="nt"))
            nt_path = tf.name
        try:
            graph.add_nt(nt_path, graph_uri=graph_uri)
        finally:
            os.unlink(nt_path)

    return {"base": base_name, "base_id": base_id, "edges": edges,
            "children": [c[1] for c in children], "graph_uri": graph_uri}
