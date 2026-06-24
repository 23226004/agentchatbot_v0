"""Qdrant 구현 — VectorRepository.

v1: dense(1024, cosine) named vector + payload 인덱스. sparse named vector는
컬렉션에 미리 정의해 두되(후속 하이브리드 대비), v1 검색은 dense-only.
"""

from __future__ import annotations

from qdrant_client import QdrantClient, models

from legal_core import ids
from legal_core.schemas import Chunk, DenseSparse, Hit

DENSE = "dense"
SPARSE = "sparse"


class QdrantVector:
    """VectorRepository Protocol 구현."""

    def __init__(self, url: str, collection: str = "law_articles", dim: int = 1024) -> None:
        self.client = QdrantClient(url=url)
        self.collection = collection
        self.dim = dim

    def ensure_collection(self) -> None:
        """컬렉션 + payload 인덱스 생성(멱등). Design §3.4 B-2."""
        if not self.client.collection_exists(self.collection):
            self.client.create_collection(
                self.collection,
                vectors_config={DENSE: models.VectorParams(size=self.dim, distance=models.Distance.COSINE)},
                sparse_vectors_config={SPARSE: models.SparseVectorParams()},  # 후속 하이브리드 대비
            )
        # 필터 대상 payload 인덱스 (없으면 full-scan)
        for field, schema in (
            ("is_current", models.PayloadSchemaType.BOOL),
            ("eff_date", models.PayloadSchemaType.KEYWORD),
            ("resource_id", models.PayloadSchemaType.KEYWORD),
            ("statute", models.PayloadSchemaType.KEYWORD),
        ):
            try:
                self.client.create_payload_index(self.collection, field_name=field, field_schema=schema)
            except Exception:
                pass  # 이미 존재 — 멱등

    def upsert(self, chunks: list[Chunk], vectors: list[DenseSparse]) -> None:
        if len(chunks) != len(vectors):
            raise ValueError("chunks/vectors 길이 불일치")
        points = []
        for c, v in zip(chunks, vectors):
            vec: dict = {DENSE: v.dense}
            if v.sparse:                       # sparse 있으면 하이브리드 슬롯 채움
                vec[SPARSE] = models.SparseVector(
                    indices=list(v.sparse.keys()), values=list(v.sparse.values()))
            points.append(models.PointStruct(
                id=ids.point_id(c.uri, c.seq), vector=vec,
                payload={
                    "uri": c.uri, "resource_id": c.resource_id, "statute": c.statute,
                    "article_no": c.article_no, "article_key": c.article_key,
                    "eff_date": c.eff_date, "ministry": c.ministry,
                    "text": c.text, "is_current": c.is_current, "seq": c.seq,
                    "article_text": c.article_text or c.text,
                },
            ))
        self.client.upsert(self.collection, points=points)

    def search(self, q: DenseSparse, k: int, flt: dict | None = None) -> list[Hit]:
        qf = _build_filter(flt)
        if q.sparse:                           # 하이브리드: dense + sparse prefetch → RRF 융합
            pre = max(k * 4, 50)
            res = self.client.query_points(
                self.collection,
                prefetch=[
                    models.Prefetch(query=q.dense, using=DENSE, limit=pre, filter=qf),
                    models.Prefetch(
                        query=models.SparseVector(indices=list(q.sparse.keys()),
                                                  values=list(q.sparse.values())),
                        using=SPARSE, limit=pre, filter=qf),
                ],
                query=models.FusionQuery(fusion=models.Fusion.RRF),
                limit=k, with_payload=True,
            )
        else:                                  # dense-only 폴백
            res = self.client.query_points(
                self.collection, query=q.dense, using=DENSE, limit=k,
                query_filter=qf, with_payload=True)
        return [Hit(uri=p.payload["uri"], score=p.score, payload=p.payload) for p in res.points]


def _build_filter(flt: dict | None) -> models.Filter | None:
    """{"is_current": True} → Qdrant Filter. v1은 정확일치만.

    as-of 범위(`eff_date<=`)는 슬라이스6에서 DatetimeRange로 추가.
    """
    if not flt:
        return None
    must = [
        models.FieldCondition(key=key, match=models.MatchValue(value=val))
        for key, val in flt.items()
        if not key.endswith("<=")   # 범위 필터는 v1 미지원(슬라이스6)
    ]
    return models.Filter(must=must) if must else None
