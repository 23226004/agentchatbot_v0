"""tools — 에이전트가 사용하는 도구 레지스트리.

새 도구는 모듈을 추가하고 아래 get_tools()에 등록만 하면 된다.
"""

from langchain_core.tools import BaseTool

from src.tools.law import get_law_articles, search_law
from src.tools.sample import calculator, current_time, text_stats


def get_tools() -> list[BaseTool]:
    """에이전트에 바인딩할 도구 목록."""
    return [
        calculator,
        current_time,
        text_stats,
        search_law,
        get_law_articles,
    ]
