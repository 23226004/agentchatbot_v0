"""QdrantVector 하이브리드 경로 단위테스트 (fake client, 서버·모델 불필요).

v4 간판기능: sparse upsert + dense/sparse prefetch → RRF 융합. 라이브 외엔 미검증이던 갭.
"""

from __future__ import annotations

import legal_infra.vector_qdrant as vq
from qdrant_client import models
from legal_core.schemas import Chunk, DenseSparse


class _FakeClient:
    def __init__(self):
        self.upserted = None
        self.qkw = None

    def collection_exists(self, c): return True
    def create_payload_index(self, *a, **k): pass

    def upsert(self, collection, points): self.upserted = points

    def query_points(self, collection, **kw):
        self.qkw = kw
        class R: points = []
        return R()


def _chunk(text="거실"):
    return Chunk(uri="u", resource_id="r", statute="건축법", article_no="제2조",
                 article_key="k", eff_date="2026-02-27", ministry="x", text=text,
                 is_current=True, seq=0, article_text="전체")


def _vec(monkeypatch):
    monkeypatch.setattr(vq, "QdrantClient", lambda url: _FakeClient())
    return vq.QdrantVector("http://x")


def test_upsert_writes_sparse_slot(monkeypatch):
    v = _vec(monkeypatch)
    v.upsert([_chunk()], [DenseSparse(dense=[0.1, 0.2], sparse={5: 0.3, 9: 0.7})])
    vec = v.client.upserted[0].vector
    assert "dense" in vec and isinstance(vec["sparse"], models.SparseVector)
    assert list(vec["sparse"].indices) == [5, 9]
    assert v.client.upserted[0].payload["article_text"] == "전체"


def test_upsert_dense_only_no_sparse_slot(monkeypatch):
    v = _vec(monkeypatch)
    v.upsert([_chunk()], [DenseSparse(dense=[0.1, 0.2], sparse={})])
    assert "sparse" not in v.client.upserted[0].vector   # 빈 sparse면 슬롯 미생성


def test_search_hybrid_uses_rrf_prefetch(monkeypatch):
    v = _vec(monkeypatch)
    v.search(DenseSparse(dense=[0.1, 0.2], sparse={5: 0.3}), k=8, flt={"is_current": True})
    kw = v.client.qkw
    assert "prefetch" in kw and len(kw["prefetch"]) == 2          # dense + sparse
    assert isinstance(kw["query"], models.FusionQuery)            # RRF 융합
    assert kw["query"].fusion == models.Fusion.RRF


def test_search_dense_only_no_prefetch(monkeypatch):
    v = _vec(monkeypatch)
    v.search(DenseSparse(dense=[0.1, 0.2], sparse={}), k=8)
    kw = v.client.qkw
    assert "prefetch" not in kw and kw.get("using") == "dense"    # dense 폴백


def test_supersede_demotes_other_eff_dates(monkeypatch):
    """supersede: keep_eff_date 가 아닌 시행본만 is_current=False 강등(삭제 아님 — as-of 보존)."""
    calls = {}

    class _C(_FakeClient):
        def set_payload(self, collection, payload, points, wait=False):
            calls["payload"] = payload
            calls["filter"] = points

    v = vq.QdrantVector.__new__(vq.QdrantVector)
    v.client, v.collection, v.dim = _C(), "law_articles", 1024
    v.supersede("001823", "2026-02-27")

    assert calls["payload"] == {"is_current": False}            # 강등(삭제 아님)
    f = calls["filter"]
    # resource_id == 대상 (must), eff_date == keep 는 제외 (must_not)
    assert f.must[0].key == "resource_id" and f.must[0].match.value == "001823"
    assert f.must_not[0].key == "eff_date" and f.must_not[0].match.value == "2026-02-27"
