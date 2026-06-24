"""_build_filter 단위테스트 (서버 불필요)."""

from __future__ import annotations

from legal_infra.vector_qdrant import _build_filter


def test_none_and_empty():
    assert _build_filter(None) is None
    assert _build_filter({}) is None


def test_is_current():
    f = _build_filter({"is_current": True})
    assert f is not None and len(f.must) == 1


def test_range_key_skipped_v1():
    """as-of 범위(eff_date<=)는 v1 미지원 → skip(슬라이스6)."""
    assert _build_filter({"eff_date<=": "2025-01-01"}) is None
    f = _build_filter({"is_current": True, "eff_date<=": "2025-01-01"})
    assert f is not None and len(f.must) == 1          # is_current만 남음
