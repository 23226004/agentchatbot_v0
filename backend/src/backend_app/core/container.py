"""DI 컨테이너 — env로 구현체 선택·주입 (Design §9.2 / conversation-store §6).

검색(RetrievalService)은 `legal_infra.provider` 로 일원화(agent 와 공유, 슬라이스8).
conversation-store 영속(풀·checkpointer·repository)은 여기서 조립한다(합성 루트).
"""

from __future__ import annotations

from backend_app.db import build_pool, database_url, run_migrations
from backend_app.repositories import ConversationRepository

__all__ = [
    "build_embedding", "build_retrieval_service",
    "build_pool", "database_url", "run_migrations",
    "build_conversation_repository", "build_checkpointer",
]


# 검색 DI(legal_infra)는 **lazy** — conversation-store 영속만 쓰는 부팅 경로가 검색 스택
# (rdflib/qdrant/requests) 전체를 import 비용으로 떠안지 않도록(교차검증 지적). 검색을 실제
# 조립할 때만 import.
def build_embedding(*args, **kwargs):
    from legal_infra import build_embedding as _impl
    return _impl(*args, **kwargs)


def build_retrieval_service(*args, **kwargs):
    from legal_infra import build_retrieval_service as _impl
    return _impl(*args, **kwargs)


def build_conversation_repository(pool) -> ConversationRepository:
    """transcript 영속 저장소. 장수명 풀을 주입받는다(합성 루트가 풀 1개 생성)."""
    return ConversationRepository(pool)


def build_checkpointer(pool):
    """LangGraph PostgresSaver(같은 DB·schema, checkpoint* 4테이블). 풀 주입(§6).

    배포 1회 `saver.setup()`(checkpoint* 테이블 생성)을 **합성 루트가 부팅 시 호출**해야 한다
    (이 함수는 saver 만 만든다 — 슬라이스5 부팅 시퀀스에서 run_migrations·setup·reconcile 배선).
    장수명 풀 사용(`from_conn_string` 금지). agent 는 이 checkpointer 를 외부주입(MemorySaver 대체).
    """
    from langgraph.checkpoint.postgres import PostgresSaver

    return PostgresSaver(pool)
