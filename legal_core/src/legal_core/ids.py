"""canonical IRI 생성 + 안정 ID (Design v0.3.1 §3.1, R1).

법령 정체성은 law.go.kr 법령ID 기반. 조문 URI는 **가지번호 필수**(R1: 제4조의2 등
가지조문이 흔해 조문번호만으로는 비유일 → point.id 충돌).
"""

from __future__ import annotations

import uuid

# 프로젝트 고정 네임스페이스 (deterministic UUIDv5용). 임의 변경 금지.
NS = uuid.UUID("6f4b2e1a-0c3d-5a7b-9e2f-1a2b3c4d5e6f")

BASE = "https://2026agent.kr/law"


def resource_iri(law_id: str) -> str:
    """LegalResource (법령 정체성, 시행일자 무관)."""
    return f"{BASE}/법령/{law_id}"


def expression_iri(law_id: str, eff_date: str) -> str:
    """LegalExpression (특정 시행본). eff_date = 'YYYYMMDD' 또는 'YYYY-MM-DD'."""
    return f"{resource_iri(law_id)}/{eff_date}"


def article_segment(article_no: int | str, branch_no: int | str | None = None) -> str:
    """조문 표시 세그먼트. 가지조문이면 '의{n}' 부착 (R1)."""
    seg = f"제{article_no}조"
    if branch_no not in (None, "", 0, "0"):
        seg += f"의{branch_no}"
    return seg


def article_iri(
    law_id: str,
    eff_date: str,
    article_no: int | str,
    branch_no: int | str | None = None,
) -> str:
    """Article IRI = LegalExpression/제{n}조[의{m}]. 가지번호 포함으로 유일성 보장."""
    return f"{expression_iri(law_id, eff_date)}/{article_segment(article_no, branch_no)}"


def point_id(article_iri_: str, seq: int = 0) -> str:
    """Qdrant point.id = UUIDv5(NS, uri). seq>0이면 분할 서브청크(uri#p{seq})로 유일.

    seq=0은 uri 그대로 해싱(citation id ≡ 조문 point.id 단일 규칙 유지).
    """
    key = article_iri_ if seq == 0 else f"{article_iri_}#p{seq}"
    return str(uuid.uuid5(NS, key))
