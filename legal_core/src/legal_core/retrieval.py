"""RetrievalService — 검색·관계확장 조립 (Design v0.3.1 §5).

embed → hybrid search → (rerank) → bounded expand → AnswerContext.
GPT 는 호출하지 않는다(역할경계 §6.3). reranker 는 v1 선택(없으면 생략).

Protocol(embedding/vector/graph/reranker)에만 의존하는 순수 오케스트레이션이라
legal_core 에 둔다 — backend·agent 가 동일 서비스를 주입받아 쓴다(§9.1).
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


def format_for_llm(ctx: AnswerContext) -> str:
    """AnswerContext.articles 를 LLM 입력 텍스트로 직렬화 (content_and_artifact 의 content).

    각 조문 앞에 `[id: {LawRef.id}]` 라벨을 붙여, LLM 이 본문에 `[[cite:{id}]]` 를
    주입할 수 있게 한다(system prompt 규칙·Design §6.3 C-2). artifact(구조체)는
    호출자가 별도로 RunService 에 넘겨 citation.added 를 방출한다.
    """
    if not ctx.articles:
        return "검색 결과: 근거 조문 없음. 데이터베이스에서 관련 조문을 찾지 못했습니다."

    blocks = [f"질의: {ctx.query}", f"근거 조문 {len(ctx.articles)}건:"]
    for ref in ctx.articles:
        body = ref.article_text or ref.snippet
        blocks.append(f"\n[id: {ref.id}] 《{ref.title}》 {ref.ref} (시행 {ref.eff_date})\n{body}")
    return "\n".join(blocks)


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
        # **폴백(설계 §8)**: SPARQL/Fuseki 오류·타임아웃 시 관계확장만 생략하고 벡터 검색결과는 유지
        # (그래프 장애가 검색 가용성을 0으로 만들지 않게 — 교차검증 발견).
        res_iris = list({ids.resource_iri(h.payload["resource_id"]) for h in hits})
        try:
            rels = self.graph.expand(res_iris, predicates=EXPAND_PREDICATES_V1, depth=1, limit=20)
        except Exception:  # noqa: BLE001 — 관계확장은 부가정보. 실패해도 검색만으로 답변.
            rels = []
        return AnswerContext(articles=[_to_lawref(h) for h in hits], relations=rels, query=query)
