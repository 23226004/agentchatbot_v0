"""LocalFlagEmbedding sparse 변환 단위테스트 (fake model, 모델로드 불필요)."""

from __future__ import annotations

from legal_infra.embedding_flag import LocalFlagEmbedding


class _FakeModel:
    def encode(self, texts, **kw):
        return {
            "dense_vecs": [[0.1, 0.2, 0.3] for _ in texts],
            # 양수·0·음수 가중치 혼재 — w>0만 남아야
            "lexical_weights": [{"5": 0.3, "9": 0.0, "12": -0.1, "7": 0.8} for _ in texts],
        }


def _emb():
    e = LocalFlagEmbedding()
    e._model = _FakeModel()      # 모델 로드 우회
    return e


def test_dense_and_sparse_conversion():
    out = _emb().embed(["a", "b"])
    assert len(out) == 2
    assert out[0].dense == [0.1, 0.2, 0.3]
    # 0·음수 배제, str 토큰키 → int
    assert out[0].sparse == {5: 0.3, 7: 0.8}


def test_empty():
    assert _emb().embed([]) == []
