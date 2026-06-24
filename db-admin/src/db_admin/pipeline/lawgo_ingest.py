"""법령 현행 본문 인제스천 (Design v0.3.1 §7.1, 슬라이스4).

law.go.kr API → 조문 파싱 → RDF(ELI) + 조 청크 임베딩 → Fuseki/Qdrant 적재.
멱등: named graph per Expression(GSP PUT 교체), Qdrant point.id=UUIDv5(uri).
"""

from __future__ import annotations

import os
import tempfile

from rdflib import Graph, Literal, Namespace, URIRef
from rdflib.namespace import RDF, XSD, DCTERMS

from legal_core import ids
from legal_core.lawgo import ArticleRec, LawMeta, parse_law_service
from legal_core.schemas import Chunk
from legal_core.text import split_windows

from db_admin.lawgo_client import LawGoClient

ELI = Namespace("http://data.europa.eu/eli/ontology#")
LO = Namespace("https://2026agent.kr/ontology#")
LAW = Namespace("http://www.aihub.or.kr/kb/law/")


def _iso(yyyymmdd: str) -> str:
    s = yyyymmdd.strip()
    return f"{s[0:4]}-{s[4:6]}-{s[6:8]}" if len(s) == 8 else s


def _build_graph(meta: LawMeta, arts: list[ArticleRec]) -> tuple[Graph, str]:
    """ELI 트리플 그래프 + Expression named graph URI 반환."""
    g = Graph()
    res = URIRef(ids.resource_iri(meta.law_id))
    expr = URIRef(ids.expression_iri(meta.law_id, meta.eff_date))

    g.add((res, RDF.type, ELI.LegalResource))
    g.add((res, DCTERMS.title, Literal(meta.statute)))
    g.add((res, LO.lawId, Literal(meta.law_id)))

    g.add((expr, RDF.type, ELI.LegalExpression))
    g.add((expr, ELI.realizes, res))
    g.add((expr, ELI.date_publication, Literal(_iso(meta.eff_date), datatype=XSD.date)))
    g.add((expr, LAW.statuteName, Literal(meta.statute)))

    for a in arts:
        art = URIRef(ids.article_iri(meta.law_id, meta.eff_date, a.article_no, a.branch_no))
        g.add((expr, LO.hasArticle, art))
        g.add((art, RDF.type, LAW.Article))
        g.add((art, LAW.articleName, Literal(ids.article_segment(a.article_no, a.branch_no))))
        if a.title:
            g.add((art, LAW.articleTitle, Literal(a.title)))
        g.add((art, LAW.fullText, Literal(a.text)))

    graph_uri = f"https://2026agent.kr/law/graph/{meta.law_id}/{meta.eff_date}"
    return g, graph_uri


def _build_chunks(meta: LawMeta, arts: list[ArticleRec]) -> list[Chunk]:
    chunks: list[Chunk] = []
    for a in arts:
        if not a.text.strip():
            continue
        seg = ids.article_segment(a.article_no, a.branch_no)
        art_uri = ids.article_iri(meta.law_id, meta.eff_date, a.article_no, a.branch_no)
        # 긴 조문은 슬라이딩 분할 → 서브청크(seq), uri는 조문 단위 유지(citation/graph 조인키)
        for seq, window in enumerate(split_windows(a.text)):
            chunks.append(Chunk(
                uri=art_uri, resource_id=meta.law_id, statute=meta.statute, article_no=seg,
                article_key=a.article_key, eff_date=_iso(meta.eff_date),
                ministry=meta.ministry, text=window, is_current=True, seq=seq,
                article_text=a.text,        # 조문 전체(답변용; 윈도우와 별개)
            ))
    return chunks


def ingest_law(name: str, *, client: LawGoClient, graph, vector, embedding) -> dict:
    """법령명(정확) → 현행 본문 적재. 반환: 요약 통계."""
    found = client.resolve_current(name)
    if not found:
        raise RuntimeError(f"'{name}' 현행 유일해소 실패(모호/미존재) — 적재 중단(엉뚱연결 방지)")
    mst = found["법령일련번호"]
    payload = client.fetch_law(mst)
    meta, arts = parse_law_service(payload)

    # 1) RDF → named graph 교체 적재
    g, graph_uri = _build_graph(meta, arts)
    with tempfile.NamedTemporaryFile("w", suffix=".nt", delete=False, encoding="utf-8") as tf:
        tf.write(g.serialize(format="nt"))
        nt_path = tf.name
    try:
        n_triples = graph.add_nt(nt_path, graph_uri=graph_uri)
    finally:
        os.unlink(nt_path)

    # 2) 조 청크 임베딩 → Qdrant
    chunks = _build_chunks(meta, arts)
    vector.ensure_collection()
    vectors = embedding.embed([c.text for c in chunks])
    vector.upsert(chunks, vectors)

    return {
        "statute": meta.statute, "law_id": meta.law_id, "eff_date": _iso(meta.eff_date),
        "articles": len(arts), "chunks": len(chunks), "triples": n_triples,
        "graph_uri": graph_uri,
    }
