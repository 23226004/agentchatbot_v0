"""RemoteEmbedding 배치경계·에러경로 단위테스트 (HTTP 모킹)."""

from __future__ import annotations

import pytest

import legal_infra.embedding_remote as m
from legal_infra.embedding_remote import RemoteEmbedding


class _Resp:
    def __init__(self, n): self._n = n
    def raise_for_status(self): pass
    def json(self): return {"data": [{"index": i, "embedding": [0.1]} for i in range(self._n)]}


def test_char_budget_batching(monkeypatch):
    calls = []
    monkeypatch.setattr(m.requests, "post",
                        lambda url, json, timeout: calls.append(json["input"]) or _Resp(len(json["input"])))
    e = RemoteEmbedding("http://x/v1", char_budget=10)
    out = e.embed(["aaaa", "bbbb", "cccc"])     # 4+4=8≤10 → batch1, +4>10 → batch2
    assert len(out) == 3
    assert calls == [["aaaa", "bbbb"], ["cccc"]]   # 글자예산 경계에서 분리


def test_index_order_preserved(monkeypatch):
    # 서버가 역순 index로 줘도 입력 순서로 복원
    def post(url, json, timeout):
        class R:
            def raise_for_status(self): pass
            def json(self): return {"data": [{"index": 1, "embedding": [2.0]},
                                             {"index": 0, "embedding": [1.0]}]}
        return R()
    monkeypatch.setattr(m.requests, "post", post)
    out = RemoteEmbedding("http://x/v1").embed(["a", "b"])
    assert out[0].dense == [1.0] and out[1].dense == [2.0]


def test_count_mismatch_raises(monkeypatch):
    monkeypatch.setattr(m.requests, "post", lambda url, json, timeout: _Resp(1))  # 2 요청에 1 응답
    with pytest.raises(RuntimeError):
        RemoteEmbedding("http://x/v1").embed(["a", "b"])


def test_empty():
    assert RemoteEmbedding("http://x/v1").embed([]) == []
