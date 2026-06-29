"""legal_core.ids 단위테스트 — R1(가지조문 유일성) 회귀 방지 포함."""

from __future__ import annotations

from legal_core import ids
from legal_core.schemas import LawRef


def test_branch_article_uri_is_unique():
    """R1: 제4조 vs 제4조의2 는 다른 URI·다른 point.id."""
    a4 = ids.article_iri("001823", "20260227", 4)
    a4_2 = ids.article_iri("001823", "20260227", 4, 2)
    assert a4 != a4_2
    assert ids.point_id(a4) != ids.point_id(a4_2)
    assert a4_2.endswith("제4조의2")


def test_point_id_deterministic():
    uri = ids.article_iri("001823", "20260227", 2)
    assert ids.point_id(uri) == ids.point_id(uri)  # 멱등


def test_resource_vs_expression():
    assert ids.resource_iri("001823").endswith("법령/001823")
    assert ids.expression_iri("001823", "20260227").endswith("001823/20260227")


def test_article_segment_no_branch():
    assert ids.article_segment(2) == "제2조"
    assert ids.article_segment(4, None) == "제4조"
    assert ids.article_segment(4, 0) == "제4조"   # 0/None 은 가지 아님
    assert ids.article_segment(4, 2) == "제4조의2"


def test_lawref_to_citation():
    uri = ids.article_iri("001823", "20260227", 2)
    ref = LawRef(
        id=ids.point_id(uri), kind="law", title="건축법", ref="건축법 제2조",
        snippet="거실이란...", url="http://law.go.kr/x", uri=uri,
        resource_id="001823", eff_date="2026-02-27", score=0.9,
    )
    c = ref.to_citation()
    assert set(c) == {"id", "kind", "title", "ref", "snippet", "url"}
    assert c["id"] == ids.point_id(uri)   # citation id ≡ point.id 단일 규칙


def test_eff_date_format_normalized_to_single_id():
    """'YYYYMMDD' 와 'YYYY-MM-DD' 가 같은 시행본이면 동일 article IRI·point.id (멱등, 교차검증)."""
    raw = ids.article_iri("001823", "20260227", 2)
    dash = ids.article_iri("001823", "2026-02-27", 2)
    assert raw == dash
    assert ids.point_id(raw) == ids.point_id(dash)
    # 기존 raw 형식 데이터의 id 는 불변(정규화가 raw 를 바꾸지 않음)
    assert raw.endswith("/20260227/제2조")
