"""split_windows 경계 테스트 — 임베딩 512토큰 초과 회귀 방지."""

from __future__ import annotations

from legal_core.text import MAX_CHARS, OVERLAP, split_windows


def test_short_single_window():
    assert split_windows("가" * 100) == ["가" * 100]
    assert split_windows("가" * MAX_CHARS) == ["가" * MAX_CHARS]   # 정확히 경계


def test_empty():
    assert split_windows("") == []
    assert split_windows("   ") == []


def test_long_splits_with_overlap():
    text = "".join(chr(0xAC00 + (i % 100)) for i in range(MAX_CHARS + 200))  # MAX+200
    w = split_windows(text)
    assert len(w) >= 2
    assert all(len(x) <= MAX_CHARS for x in w)          # 모든 윈도우 ≤ cap (임베딩 안전)
    # 겹침: 두번째 윈도우 시작이 첫 윈도우 끝보다 overlap 만큼 앞
    step = MAX_CHARS - OVERLAP
    assert w[1] == text[step:step + MAX_CHARS]


def test_covers_full_text():
    text = "가" * (MAX_CHARS * 2)
    joined_last = split_windows(text)[-1]
    assert text.endswith(joined_last)                  # 마지막 윈도우가 끝까지 덮음
