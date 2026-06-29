"""format_for_llm 계약 — 조문마다 [id: ...] 라벨(=[[cite]] 주입 근거). Design §6.3 C-2."""

from __future__ import annotations

from legal_core import format_for_llm
from legal_core.schemas import AnswerContext, LawRef


def _ref(rid: str, no: str) -> LawRef:
    return LawRef(
        id=rid, kind="law", title="건축법", ref=f"건축법 {no}",
        snippet="발췌", url="https://www.law.go.kr/", uri=f"u-{rid}",
        resource_id="001823", eff_date="2026-02-27", score=1.0,
        article_text=f"{no} 본문 전체",
    )


def test_each_article_labeled_with_id():
    ctx = AnswerContext(
        articles=[_ref("ID1", "제2조"), _ref("ID2", "제53조")],
        query="거실의 정의",
    )
    text = format_for_llm(ctx)
    assert "[id: ID1]" in text and "[id: ID2]" in text
    assert "건축법 제2조" in text
    assert "제2조 본문 전체" in text          # article_text(전체)가 들어가야 답변 생성 가능
    assert "거실의 정의" in text              # 질의 에코


def test_empty_context_says_no_basis():
    text = format_for_llm(AnswerContext(articles=[], query="없는질의"))
    assert "근거 조문 없음" in text
    assert "[id:" not in text                 # 라벨이 없어야 LLM 이 위조 인용 못 함
