"""국가법령정보센터(law.go.kr) OpenAPI 도구.

LAW_API_OC 환경변수(기관 OC 코드)로만 인증한다 (Zero-Trust: 비밀/식별자는 env에서).
- search_law       : 법령명·키워드로 현행 법령 검색 → MST(법령일련번호) 확보
- get_law_articles : MST로 조문 본문 조회 (조문→항→호 평탄화, 전체 반환)

실제 도구 추가 패턴의 첫 사례. DB가 아닌 외부 공개 API라 tools/에서 직접 호출한다.
"""

from __future__ import annotations

import os
from typing import Any

import requests
from langchain_core.tools import tool

_BASE = "http://www.law.go.kr/DRF"
_TIMEOUT = 20


def _oc() -> str:
    oc = os.environ.get("LAW_API_OC", "").strip()
    if not oc:
        raise RuntimeError(
            "환경변수 LAW_API_OC 가 필요합니다 (국가법령정보센터 OpenAPI OC 코드)."
        )
    return oc


def _get(path: str, params: dict[str, Any]) -> dict[str, Any]:
    """law.go.kr DRF 엔드포인트를 JSON으로 호출한다."""
    full = {"OC": _oc(), "type": "JSON", **params}
    resp = requests.get(f"{_BASE}/{path}", params=full, timeout=_TIMEOUT)
    resp.raise_for_status()
    return resp.json()


def _as_list(value: Any) -> list[Any]:
    """OpenAPI는 항목이 1개면 dict, 여러 개면 list로 준다 → 항상 list로 정규화."""
    if value is None or value == "":
        return []
    return value if isinstance(value, list) else [value]


def _flatten_article(unit: dict[str, Any]) -> str:
    """조문단위(조문 → 항 → 호)를 사람이 읽는 텍스트 한 덩어리로 평탄화한다."""
    lines: list[str] = []

    head = unit.get("조문내용")
    if isinstance(head, str) and head.strip():
        lines.append(head.strip())

    for hang in _as_list(unit.get("항")):
        if not isinstance(hang, dict):
            continue
        h = hang.get("항내용")
        if isinstance(h, str) and h.strip():
            lines.append(f"  {h.strip()}")
        for ho in _as_list(hang.get("호")):
            if not isinstance(ho, dict):
                continue
            ho_text = ho.get("호내용")
            if isinstance(ho_text, str) and ho_text.strip():
                lines.append(f"    {ho_text.strip()}")
            # 호 아래 목(目)까지 있으면 함께 펼친다
            for mok in _as_list(ho.get("목")):
                if isinstance(mok, dict):
                    m = mok.get("목내용")
                    if isinstance(m, str) and m.strip():
                        lines.append(f"      {m.strip()}")

    return "\n".join(lines)


def _search_law_raw(query: str, display: int) -> list[dict[str, Any]]:
    data = _get(
        "lawSearch.do",
        {"target": "law", "query": query, "display": max(1, min(display, 100))},
    )
    return _as_list(data.get("LawSearch", {}).get("law"))


@tool
def search_law(query: str, display: int = 10) -> str:
    """**법령 이름**으로 현행 대한민국 법령을 검색한다 (제목 검색 전용).

    중요: 이 API는 법령 '제목'만 매칭한다. '거실', '주차' 같은 조문 내용/키워드로는
    검색되지 않는다. 따라서 query 에는 법령명만 넣어라 (예: '건축법', '주차장법').
    조문 내용을 찾으려면: 먼저 이 도구로 법령의 MST 를 얻고, get_law_articles(mst, keyword=...)
    로 본문에서 해당 키워드 조문을 추려라.

    Args:
        query: 법령명 (예: '건축법'). 조문 키워드를 붙이지 말 것.
        display: 반환할 최대 건수 (기본 10)
    """
    try:
        laws = _search_law_raw(query, display)
        # 폴백: 법령명에 키워드를 덧붙여 0건이면, 앞쪽 '○○법/령/규칙' 부분만으로 재검색
        if not laws and " " in query.strip():
            import re

            m = re.search(r"\S*?(?:법|령|규칙|조례|규정)", query.strip())
            if m and m.group(0) != query.strip():
                laws = _search_law_raw(m.group(0), display)
                if laws:
                    query = m.group(0)
    except Exception as exc:  # noqa: BLE001 - 도구는 예외를 문자열로 돌려준다
        return f"법령 검색 오류: {exc}"

    if not laws:
        return (
            f"'{query}' 에 대한 검색 결과가 없습니다. "
            "이 검색은 '법령명'만 찾습니다 — 조문 키워드 말고 법령 이름만 넣어보세요(예: '건축법')."
        )

    lines = [f"'{query}' 검색 결과 {len(laws)}건:"]
    for law in laws:
        lines.append(
            f"- {law.get('법령명한글')} "
            f"({law.get('법령구분명')}, 소관 {law.get('소관부처명')}) "
            f"| MST={law.get('법령일련번호')} | 시행일 {law.get('시행일자')} "
            f"| {law.get('현행연혁코드')}"
        )
    return "\n".join(lines)


@tool
def get_law_articles(mst: str, keyword: str = "") -> str:
    """MST(법령일련번호)로 법령 본문의 전체 조문을 조회한다.

    조문 → 항 → 호 구조를 모두 펼쳐 반환한다.
    keyword 를 주면 그 단어가 포함된 조문만 추린다(비우면 전체).

    Args:
        mst: search_law 가 돌려준 법령일련번호(MST)
        keyword: 조문 필터 키워드 (선택, 기본 전체 반환)
    """
    try:
        data = _get("lawService.do", {"target": "law", "MST": str(mst)})
    except Exception as exc:  # noqa: BLE001
        return f"법령 본문 조회 오류: {exc}"

    law = data.get("법령", {})
    basic = law.get("기본정보", {})
    units = _as_list(law.get("조문", {}).get("조문단위"))
    if not units:
        return f"MST={mst} 의 조문을 찾을 수 없습니다."

    name = basic.get("법령명_한글", "")
    eff = basic.get("시행일자", "")
    header = f"《{name}》 (시행 {eff}, MST={mst})"

    blocks: list[str] = []
    for unit in units:
        text = _flatten_article(unit)
        if not text:
            continue
        if keyword and keyword not in text:
            continue
        blocks.append(text)

    if not blocks:
        return f"{header}\n'{keyword}' 가 포함된 조문이 없습니다."

    body = "\n\n".join(blocks)
    note = f" (키워드 '{keyword}' 필터: {len(blocks)}개 조문)" if keyword else ""
    return f"{header}{note}\n\n{body}"
