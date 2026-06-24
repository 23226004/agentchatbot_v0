"""RetrievalService 단위테스트 (페이크 repo, 서버 불필요) — Design §11."""

from __future__ import annotations

import pytest

from legal_core import ids
from legal_core.schemas import DenseSparse, Hit

from backend_app.services.retrieval import RetrievalService

URI2 = ids.article_iri("001823", "20260227", 2)
URI53 = ids.article_iri("001823", "20260227", 53)
ART2_FULL = "제2조(정의) ... 6. \"거실\"이란 건축물 안에서 ... 사용되는 방을 말한다."


def _payload(uri, art_no, text, article_text=None):
    return {"uri": uri, "resource_id": "001823", "statute": "건축법",
            "article_no": art_no, "article_key": "k", "eff_date": "2026-02-27",
            "ministry": "국토교통부", "text": text, "is_current": True, "seq": 0,
            "article_text": article_text or text}


class FakeEmb:
    def embed(self, texts): return [DenseSparse(dense=[0.1] * 4, sparse={}) for _ in texts]


class FakeGraph:
    def expand(self, uris, predicates, depth=1, limit=20): return []


class FakeVecSubchunks:
    """제2조의 분할 서브청크 2건(정의 윈도우는 벡터점수 낮음) + 제53조(높음)."""
    def search(self, q, k, flt=None):
        return [
            Hit(uri=URI53, score=0.95, payload=_payload(URI53, "제53조", "지하층에 거실 설치 금지")),
            Hit(uri=URI2, score=0.9, payload=_payload(URI2, "제2조", "제2조 기타 항목", ART2_FULL)),
            Hit(uri=URI2, score=0.3, payload=_payload(URI2, "제2조", "거실이란 방을 말한다", ART2_FULL)),
        ]


class FakeReranker:
    """'거실이란' 포함 텍스트를 최고점으로 — 정의 의도 모사."""
    def rerank(self, query, hits, k):
        scored = [(h, 0.99 if "거실이란" in h.payload["text"] else 0.1) for h in hits]
        scored.sort(key=lambda t: t[1], reverse=True)
        return [Hit(uri=h.uri, score=s, payload=h.payload) for h, s in scored[:k]]


def test_rerank_before_dedup_keeps_right_window():
    """rerank가 dedup보다 먼저 → 제2조의 '정의 윈도우'(벡터 0.3)가 reranker로 대표 생존."""
    svc = RetrievalService(FakeEmb(), FakeVecSubchunks(), FakeGraph(), reranker=FakeReranker())
    ctx = svc.retrieve("거실 정의", k=2)
    assert len(ctx.articles) == 2
    top = ctx.articles[0]
    assert top.ref == "건축법 제2조" and top.score == 0.99       # 정의 윈도우가 1위
    assert "거실" in top.article_text and "방을 말한다" in top.article_text  # 답변용 전체 본문


def test_no_reranker_dedup_on_vector_score():
    svc = RetrievalService(FakeEmb(), FakeVecSubchunks(), FakeGraph(), reranker=None)
    ctx = svc.retrieve("x", k=2)
    # reranker 없으면 벡터점수 dedup: 제53조(0.95), 제2조(0.9)
    assert [a.ref for a in ctx.articles] == ["건축법 제53조", "건축법 제2조"]


def test_lawref_citation_id_is_article_seq0():
    svc = RetrievalService(FakeEmb(), FakeVecSubchunks(), FakeGraph(), reranker=None)
    top = svc.retrieve("x", k=1).articles[0]
    assert top.id == ids.point_id(top.uri)        # citation id = 조문 point.id(seq0)


def test_as_of_raises_not_silent():
    """#4 회귀: as_of 지정 시 조용히 틀린 결과 대신 NotImplementedError."""
    svc = RetrievalService(FakeEmb(), FakeVecSubchunks(), FakeGraph(), reranker=None)
    with pytest.raises(NotImplementedError):
        svc.retrieve("x", as_of="2020-01-01")
