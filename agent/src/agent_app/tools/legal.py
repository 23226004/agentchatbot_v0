"""법령 검색 도구 — RetrievalService(GraphRAG) 경유 (FR-12, Design §6.3).

구 law.go.kr 직결 도구(search_law/get_law_articles)를 대체한다. 도구는
`response_format="content_and_artifact"` 로 (LLM용 텍스트, AnswerContext)를 동시 반환:
- content : 각 조문에 `[id: ...]` 라벨 → LLM이 본문에 `[[cite:{id}]]` 주입(system prompt 규칙)
- artifact: AnswerContext(구조체) → RunService가 LawRef[]로 citation.added 방출

서비스는 주입받거나(테스트), 없으면 첫 호출 때 env로 lazy 조립한다(legal_infra).
"""

from __future__ import annotations

from typing import Any

from langchain_core.tools import BaseTool, tool
from legal_core import format_for_llm


def _lazy_service_holder(service: Any) -> Any:
    """주입 서비스가 없으면 첫 사용 시 legal_infra.build_retrieval_service()로 조립."""

    class _Holder:
        def __init__(self) -> None:
            self._svc = service

        def get(self) -> Any:
            if self._svc is None:
                from legal_infra import build_retrieval_service

                self._svc = build_retrieval_service()
            return self._svc

    return _Holder()


def make_legal_tool(service: Any = None) -> BaseTool:
    """RetrievalService 를 묶어 법령 검색 도구를 만든다. service=None 이면 lazy 조립."""
    holder = _lazy_service_holder(service)

    @tool(response_format="content_and_artifact")
    def search_legal(query: str) -> tuple[str, Any]:
        """대한민국 법령 본문에서 질문과 관련된 **조문을 검색**한다 (GraphRAG).

        법령명·조문 키워드를 가리지 않고 자연어 질의를 그대로 넣으면 된다
        (예: '거실의 정의', '건축물 높이 제한'). 결과 각 조문에는 `[id: ...]` 라벨이
        붙는다 — 답변 본문에서 그 조문을 근거로 쓸 때 `[[cite:{id}]]` 로 인용하라.

        Args:
            query: 찾고 싶은 내용을 담은 자연어 질의 (법령명만 넣을 필요 없음)
        """
        # 입력 가드(교차검증): 빈 질의는 garbage 조문을 정상결과처럼 돌려주고(무가드 검색),
        # 초장문은 임베딩 서버 500 을 유발한다 → "일시적 오류, 재시도" 오안내 대신 명시적으로 거른다.
        q = (query or "").strip()
        if not q:
            return "검색어가 비어 있습니다. 찾고 싶은 법령 내용을 입력하세요.", None
        if len(q) > 2000:  # 단일 임베딩 입력 상한(서버 컨텍스트). 재시도로 풀리지 않으므로 명시.
            return "질의가 너무 깁니다. 핵심 키워드로 줄여서 다시 검색하세요.", None
        try:
            ctx = holder.get().retrieve(q)
        except NotImplementedError as exc:  # as_of 등 미지원은 LLM에 사유 전달(내부정보 아님)
            return f"법령 검색 미지원: {exc}", None
        except Exception:  # noqa: BLE001 — 상세 예외(내부 엔드포인트/스택)는 LLM·FE에 노출 금지
            return "법령 검색 중 일시적 오류가 발생했습니다. 잠시 후 다시 시도하세요.", None
        return format_for_llm(ctx), ctx

    return search_legal
