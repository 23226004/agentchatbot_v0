"""RetrievalService — 검색·관계확장 조립 (Design v0.3.1 §5).

embed → hybrid search → (rerank) → bounded expand → AnswerContext.
GPT는 호출하지 않는다(역할경계 §6.3). reranker는 v1 선택(없으면 생략).
"""

from __future__ import annotations

from urllib.parse import quote

from legal_core import ids
from legal_core.repositories import EXPAND_PREDICATES_V1
from legal_core.schemas import AnswerContext, Hit, LawRef


def _law_url(statute: str, article_no: str) -> str:
    return f"https://www.law.go.kr/법령/{quote(statute)}/{quote(article_no)}"


def _to_lawref(h: Hit) -> LawRef:
    p = h.payload
    full = p.get("article_text") or p.get("text", "")
    return LawRef(
        id=ids.point_id(p["uri"]),                 # seq0 — 조문 단위 citation id
        kind="law",
        title=p["statute"],
        ref=f'{p["statute"]} {p["article_no"]}',
        snippet=full[:200],                        # citation 표면용 짧은 발췌
        url=_law_url(p["statute"], p["article_no"]),
        uri=p["uri"], resource_id=p["resource_id"],
        eff_date=p["eff_date"], score=h.score,
        article_text=full,                         # 답변 생성용 조문 전체
    )


def _dedup_by_article(hits: list[Hit]) -> list[Hit]:
    """분할 서브청크(같은 조문 uri, 다른 seq) → 조문당 최고점 1건만(점수순 유지)."""
    best: dict[str, Hit] = {}
    for h in hits:
        u = h.payload["uri"]
        if u not in best or h.score > best[u].score:
            best[u] = h
    return sorted(best.values(), key=lambda h: h.score, reverse=True)


class RetrievalService:
    def __init__(self, embedding, vector, graph, reranker=None) -> None:
        self.emb = embedding
        self.vec = vector
        self.graph = graph
        self.reranker = reranker     # v1 None 가능

    def retrieve(self, query: str, as_of: str | None = None, k: int = 8) -> AnswerContext:
        # #4: as-of(시점 질의)는 슬라이스6. v1에서 지정 시 조용히 틀린 결과 대신 명확히 실패.
        if as_of is not None:
            raise NotImplementedError("as_of(시점 질의)는 슬라이스6 — v1은 현행(is_current)만 지원")
        qv = self.emb.embed([query])[0]
        hits = self.vec.search(qv, k=30, flt={"is_current": True})
        # rerank를 dedup보다 먼저 — 분할 서브청크 중 '질의에 가장 맞는 윈도우'가
        # 대표로 살아남게(아니면 본문 손실: 긴 조문의 엉뚱한 윈도우가 대표가 됨).
        if self.reranker is not None:
            hits = self.reranker.rerank(query, hits, k=30)
        hits = _dedup_by_article(hits)[:k]           # 조문당 최고점 윈도우 병합 → top-k
        # bounded 관계확장: delegatesTo는 법령(resource)간 관계 → 조문 payload의
        # resource_id로 법령 IRI를 만들어 확장(조문→법령 매핑은 payload에 이미 존재).
        res_iris = list({ids.resource_iri(h.payload["resource_id"]) for h in hits})
        rels = self.graph.expand(res_iris, predicates=EXPAND_PREDICATES_V1, depth=1, limit=20)
        return AnswerContext(articles=[_to_lawref(h) for h in hits], relations=rels, query=query)
