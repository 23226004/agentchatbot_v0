"""Repository 인터페이스 (Protocol) — 구현은 backend/db-admin의 *_qdrant/_fuseki/_flag/_tei.

Design v0.3.1 §4. services·db-admin은 이 Protocol에만 의존하고 구현은 DI로 주입한다.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from legal_core.schemas import Chunk, DenseSparse, Hit

# 관계확장 허용 술어 화이트리스트 (Design §4, C-1).
# 1차는 위임만 — citations(조문→판례)·related(용어) 등 노이즈 엣지 배제.
EXPAND_PREDICATES_V1: list[str] = ["lo:delegatesTo"]
EXPAND_PREDICATES_FULL: list[str] = ["lo:delegatesTo", "eli:realizes"]


@runtime_checkable
class EmbeddingProvider(Protocol):
    """BGE-m3 임베딩 — dense+sparse 동시 (FlagEmbedding 구현)."""

    def embed(self, texts: list[str]) -> list[DenseSparse]: ...


@runtime_checkable
class VectorRepository(Protocol):
    """Qdrant — 하이브리드(dense+sparse, RRF) 검색."""

    def upsert(self, chunks: list[Chunk], vectors: list[DenseSparse]) -> None:
        """청크+벡터를 함께 upsert (벡터는 외부에서 주입 — 임베딩 역의존 없음)."""
        ...

    def search(
        self, q: DenseSparse, k: int, flt: dict | None = None
    ) -> list[Hit]:
        """dense/sparse prefetch + RRF 융합 + payload 필터."""
        ...

    def supersede(self, resource_id: str, keep_eff_date: str) -> None:
        """개정 반영: 같은 법령의 keep_eff_date 가 **아닌** 시행본 point 를 is_current=False 로 강등.

        **삭제가 아니라 강등** — as-of(시점) 질의가 옛 시행본을 여전히 찾도록 ELI 버전을 보존하되,
        현행(is_current=True) 검색에서 옛 본문이 새 본문과 함께 노출되는 stale-current 를 막는다.
        """
        ...


@runtime_checkable
class Reranker(Protocol):
    """BGE-reranker-v2-m3 (TEI 구현)."""

    def rerank(self, query: str, hits: list[Hit], k: int) -> list[Hit]: ...


@runtime_checkable
class GraphRepository(Protocol):
    """Fuseki — RDF 적재 + bounded 관계확장 + 안전 바인딩 질의."""

    def add_nt(self, path: str, graph_uri: str | None = None) -> int:
        """N-Triples 벌크 적재(GSP PUT, named graph 멱등 교체). 적재 트리플 수 반환."""
        ...

    def expand(
        self,
        uris: list[str],
        predicates: list[str],
        depth: int = 1,
        limit: int = 20,
    ) -> list[tuple[str, str, str]]:
        """술어 화이트리스트로 bounded 1-hop 관계확장. uris는 검증된 절대 IRI만."""
        ...

    def select(self, template: str, bindings: dict) -> list[dict]:
        """타입드 바인더(.n3() 이스케이프)로 안전 SELECT. raw 문자열 보간 금지."""
        ...
