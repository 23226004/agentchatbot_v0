"""graph_fuseki 안전 바인딩 단위테스트 (서버 불필요) — Zero-Trust 회귀 방지."""

from __future__ import annotations

import pytest

from legal_infra.graph_fuseki import _curie_to_iri, _validate_iri


def test_validate_iri_accepts_allowed():
    assert _validate_iri("https://2026agent.kr/law/법령/001823").startswith("https://")
    assert _validate_iri("http://www.law.go.kr/x")


@pytest.mark.parametrize("bad", [
    "ftp://evil/x",                         # 비 http
    "javascript:alert(1)",                  # 스킴 주입
    "https://evil.com/x",                   # 허용 안 된 네임스페이스
    "법령/001823",                          # 상대
    "<https://2026agent.kr/x> } INJECT",    # SPARQL 주입 시도
    # #3 회귀: 허용 prefix지만 인젝션 문자(>·공백·중괄호) 포함 — ValueError로 차단돼야
    "https://2026agent.kr/x> } DELETE { ?s ?p ?o } #",
    "https://2026agent.kr/a b",             # 공백
])
def test_validate_iri_rejects(bad):
    with pytest.raises(ValueError):
        _validate_iri(bad)


def test_curie_predicate_injection_rejected():
    """#3: 술어 인젝션(prefix 통과 후 본문에 SPARQL) → ValueError."""
    with pytest.raises(ValueError):
        _curie_to_iri("lo:delegatesTo> } DELETE { ?s ?p ?o } #")


def test_curie_to_iri():
    assert _curie_to_iri("lo:delegatesTo") == "https://2026agent.kr/ontology#delegatesTo"
    assert _curie_to_iri("eli:realizes").endswith("ontology#realizes")


@pytest.mark.parametrize("bad", ["unknown:pred", "delegatesTo", "http://x/y"])
def test_curie_to_iri_rejects(bad):
    with pytest.raises(ValueError):
        _curie_to_iri(bad)
