"""RetrievalService — legal_core 로 이전됨 (Design v0.3.1 §9.1, 슬라이스8).

agent 도 동일 서비스를 주입받아야 해서(FR-12) backend 전용 위치에서 legal_core 로
끌어올렸다. 기존 import 경로(`backend_app.services.retrieval`) 호환을 위한 re-export.
"""

from __future__ import annotations

from legal_core.retrieval import RetrievalService, format_for_llm

__all__ = ["RetrievalService", "format_for_llm"]
