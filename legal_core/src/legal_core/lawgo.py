"""law.go.kr lawService JSON → 구조화 파싱 (순수, 외부 의존 없음).

조문단위(조 → 항 → 호 → 목)를 평탄화한다. 가지조문(조문가지번호) 보존(R1).
db-admin(현재)·agent(슬라이스8 통합 시)가 공유. (Design C-3)
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class LawMeta:
    law_id: str
    statute: str
    eff_date: str          # 원본 "YYYYMMDD"
    ministry: str
    promulgation_date: str


@dataclass(frozen=True)
class ArticleRec:
    article_no: int
    branch_no: int | None   # 가지번호 (제N조의M) — 없으면 None
    article_key: str        # 조문키 (statute 내 전역 유일)
    title: str
    text: str               # 항/호/목 평탄화 본문


def _as_list(v: Any) -> list[Any]:
    if v is None or v == "":
        return []
    return v if isinstance(v, list) else [v]


def flatten_article(unit: dict[str, Any]) -> str:
    """조문단위(조 → 항 → 호 → 목)를 텍스트 한 덩어리로."""
    lines: list[str] = []
    head = unit.get("조문내용")
    if isinstance(head, str) and head.strip():
        lines.append(head.strip())
    for hang in _as_list(unit.get("항")):
        if not isinstance(hang, dict):
            continue
        h = hang.get("항내용")
        if isinstance(h, str) and h.strip():
            lines.append(h.strip())
        for ho in _as_list(hang.get("호")):
            if not isinstance(ho, dict):
                continue
            t = ho.get("호내용")
            if isinstance(t, str) and t.strip():
                lines.append(t.strip())
            for mok in _as_list(ho.get("목")):
                if isinstance(mok, dict):
                    m = mok.get("목내용")
                    if isinstance(m, str) and m.strip():
                        lines.append(m.strip())
    return "\n".join(lines)


def _branch(unit: dict[str, Any]) -> int | None:
    b = str(unit.get("조문가지번호", "")).strip()
    return int(b) if b and b != "0" else None


def parse_law_service(payload: dict[str, Any]) -> tuple[LawMeta, list[ArticleRec]]:
    """lawService.do JSON → (LawMeta, [ArticleRec]). 조문여부=='조문'만(전문/장제목 제외)."""
    law = payload["법령"]
    basic = law["기본정보"]
    ministry = basic.get("소관부처")
    ministry = ministry.get("content", "") if isinstance(ministry, dict) else (ministry or "")
    meta = LawMeta(
        law_id=str(basic["법령ID"]).strip(),
        statute=basic["법령명_한글"].strip(),
        eff_date=str(basic["시행일자"]).strip(),
        ministry=ministry,
        promulgation_date=str(basic.get("공포일자", "")).strip(),
    )
    arts: list[ArticleRec] = []
    for u in _as_list(law.get("조문", {}).get("조문단위")):
        if u.get("조문여부") != "조문":      # 전문(장 제목 등) 제외
            continue
        try:
            no = int(str(u["조문번호"]).strip())
        except (KeyError, ValueError):
            continue
        arts.append(ArticleRec(
            article_no=no,
            branch_no=_branch(u),
            article_key=str(u.get("조문키", "")).strip(),
            title=str(u.get("조문제목", "")).strip(),
            text=flatten_article(u),
        ))
    return meta, arts


def parse_hierarchy(payload: dict[str, Any], base_name: str, base_id: str) -> list[tuple[str, str]]:
    """lsStmd(법령체계도) JSON → base 법령의 위임 하위 법령 [(법령ID, 법령명)].

    **트리 구조 기반**(이름 prefix 아님): 상하위법 트리에서 법령ID를 가진 노드 = 위임 법령.
    - 자치법규/조례(자치법규ID만 보유)는 법령ID 없음 → 자동 제외.
    - base 자신 제외. 법종이 '법률'인 노드 제외(상위 법률 — base가 시행령일 때의 부모).
    이름 휴리스틱을 버려 동일 트리 위치의 위임 부령(구조기준·설비기준 규칙 등) 누락을 해소.
    (base_name 은 시그니처 호환 위해 유지하나 필터에 사용하지 않음.)
    """
    found: dict[str, str] = {}

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            basic = node.get("기본정보")
            if isinstance(basic, dict):
                lid = str(basic.get("법령ID", "")).strip()
                name = str(basic.get("법령명", "")).strip()
                jong = (basic.get("법종구분") or {})
                jong = jong.get("content", "") if isinstance(jong, dict) else ""
                if lid and name and lid != base_id and jong != "법률":
                    found[lid] = name
            for k, v in node.items():
                if k != "기본정보":
                    walk(v)
        elif isinstance(node, list):
            for it in node:
                walk(it)

    walk(payload.get("법령체계도", {}).get("상하위법", {}))
    return sorted(found.items())
