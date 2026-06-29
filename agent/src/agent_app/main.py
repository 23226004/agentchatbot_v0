"""CLI REPL — ReAct 루프를 대화형으로 검증한다.

실행:  cd agent && python -m agent_app.main
종료:  exit / quit / Ctrl-D
"""

from __future__ import annotations

from typing import Any

try:
    from dotenv import load_dotenv
except ImportError:  # python-dotenv 미설치 시 .env 없이 진행
    def load_dotenv(*_a, **_k):  # type: ignore
        return False

from agent_app.core.agent import ReActAgent


def _render(update: dict[str, Any]) -> None:
    """stream_mode='updates' 청크를 사람이 읽기 좋게 출력."""
    for _node, payload in update.items():
        # 승인모드 스트림은 {'__interrupt__': (튜플,)} 처럼 payload 가 dict 가 아닐 수 있다 → 건너뜀.
        if not isinstance(payload, dict):
            continue
        for msg in payload.get("messages", []):
            tool_calls = getattr(msg, "tool_calls", None)
            if tool_calls:
                for call in tool_calls:
                    print(f"  🤔 도구 호출: {call['name']}({call['args']})")
            elif msg.__class__.__name__ == "ToolMessage":
                print(f"  🔧 도구 결과: {msg.content}")
            elif getattr(msg, "content", ""):
                print(f"  🟢 답변: {msg.content}")


def main() -> None:
    load_dotenv()
    agent = ReActAgent()
    s = agent.settings
    endpoint = s.llm_base_url or "api.openai.com (기본)"
    print(f"ReAct Agent | provider={s.provider} | model={s.llm_model} | {endpoint}")
    print("질문을 입력하세요 (exit/quit 으로 종료).")

    while True:
        try:
            user = input("\n질문> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if not user:
            continue
        if user in {"exit", "quit", ":q"}:
            break
        for update in agent.stream(user, thread_id="cli"):
            _render(update)


if __name__ == "__main__":
    main()
