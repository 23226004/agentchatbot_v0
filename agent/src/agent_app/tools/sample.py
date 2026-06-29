"""ReAct 루프 검증용 최소 샘플 도구.

실제 도구(법령검색 등)는 이후 이 모듈과 같은 패턴으로 추가한다.
"""

from __future__ import annotations

import ast
import operator
from datetime import datetime

from langchain_core.tools import tool

# Zero-Trust: eval() 대신 허용된 연산자만 화이트리스트로 평가한다.
_OPS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.FloorDiv: operator.floordiv,
    ast.Mod: operator.mod,
    ast.Pow: operator.pow,
    ast.USub: operator.neg,
    ast.UAdd: operator.pos,
}


def _safe_eval(node: ast.AST) -> float:
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
        return node.value
    if isinstance(node, ast.BinOp) and type(node.op) in _OPS:
        left, right = _safe_eval(node.left), _safe_eval(node.right)
        # pow-bomb DoS 가드: 9**9**9 류가 CPU/메모리를 무한 점유(예외도 못 끊음)하지 않게
        # 지수·밑 크기를 제한한다(교차검증). 일반 계산기 용도엔 충분히 넉넉한 상한.
        if type(node.op) is ast.Pow and (abs(right) > 1000 or abs(left) > 1e6):
            raise ValueError("거듭제곱이 너무 큽니다.")
        return _OPS[type(node.op)](left, right)
    if isinstance(node, ast.UnaryOp) and type(node.op) in _OPS:
        return _OPS[type(node.op)](_safe_eval(node.operand))
    raise ValueError("허용되지 않은 식입니다.")


@tool
def calculator(expression: str) -> str:
    """사칙연산과 거듭제곱을 계산한다. 예: '2 * (3 + 4) ** 2'."""
    try:
        tree = ast.parse(expression, mode="eval")
        return str(_safe_eval(tree.body))
    except Exception as exc:  # noqa: BLE001 - 도구는 예외를 문자열로 돌려준다
        return f"계산 오류: {exc}"


@tool
def current_time() -> str:
    """현재 서버 로컬 시각을 ISO 8601 형식으로 반환한다."""
    return datetime.now().isoformat(timespec="seconds")


@tool
def text_stats(text: str) -> str:
    """입력 문자열의 글자 수와 단어 수를 센다."""
    return f"글자수={len(text)}, 단어수={len(text.split())}"
