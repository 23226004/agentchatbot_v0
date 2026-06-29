"""단기 메모리 — thread_id 단위로 대화 상태를 보존하는 checkpointer.

지금은 인메모리(MemorySaver). 영속이 필요하면 동일 인터페이스의
SqliteSaver/PostgresSaver 등으로 교체하면 된다 (호출부 변경 없음).
"""

from __future__ import annotations

from langgraph.checkpoint.memory import MemorySaver


def build_checkpointer() -> MemorySaver:
    return MemorySaver()
