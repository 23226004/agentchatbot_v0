"""tools — 에이전트가 사용하는 도구 레지스트리.

새 도구는 모듈을 추가하고 아래 get_tools()에 등록만 하면 된다.
법령은 RetrievalService(GraphRAG) 경유 단일 도구로 일원화(FR-12). 구 law.go.kr
직결 도구(search_law/get_law_articles)는 레지스트리에서 제외했다(db-admin 적재 경로로 이동).
"""

from typing import Any

from langchain_core.tools import BaseTool

from agent_app.tools.legal import make_legal_tool
from agent_app.tools.sample import calculator, current_time, text_stats


def get_tools(retrieval_service: Any = None) -> list[BaseTool]:
    """에이전트에 바인딩할 도구 목록.

    retrieval_service 를 주면 법령 도구에 주입하고, 없으면 첫 호출 때 env 로 조립한다.
    """
    return [
        calculator,
        current_time,
        text_stats,
        make_legal_tool(retrieval_service),
    ]
