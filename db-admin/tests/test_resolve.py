"""resolve_current 격리 가드 단위테스트 (HTTP 모킹) — 엉뚱연결 방지 회귀."""

from __future__ import annotations

from db_admin.lawgo_client import LawGoClient


def _client_returning(laws):
    c = LawGoClient(oc="x")
    c._get = lambda path, params: {"LawSearch": {"law": laws}}   # 모킹
    return c


def test_unique_current_resolved():
    c = _client_returning([
        {"법령명한글": "건축법", "현행연혁코드": "현행", "법령ID": "001823"},
        {"법령명한글": "건축법 시행령", "현행연혁코드": "현행", "법령ID": "002118"},
    ])
    r = c.resolve_current("건축법")
    assert r and r["법령ID"] == "001823"


def test_no_exact_returns_none():
    c = _client_returning([{"법령명한글": "건축물관리법", "현행연혁코드": "현행", "법령ID": "9"}])
    assert c.resolve_current("건축법") is None        # 부분일치만 → None


def test_only_history_returns_none():
    c = _client_returning([{"법령명한글": "건축법", "현행연혁코드": "연혁", "법령ID": "x"}])
    assert c.resolve_current("건축법") is None        # 현행 없음 → None


def test_ambiguous_two_current_returns_none():
    c = _client_returning([
        {"법령명한글": "건축법", "현행연혁코드": "현행", "법령ID": "a"},
        {"법령명한글": "건축법", "현행연혁코드": "현행", "법령ID": "b"},
    ])
    assert c.resolve_current("건축법") is None        # 모호 → None(엉뚱연결 방지)


def test_dict_normalized():
    c = LawGoClient(oc="x")
    c._get = lambda p, q: {"LawSearch": {"law": {"법령명한글": "민법", "현행연혁코드": "현행", "법령ID": "1706"}}}
    assert c.resolve_current("민법")["법령ID"] == "1706"   # 단건 dict 정규화
