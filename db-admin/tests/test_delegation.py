"""build_delegation 단위테스트 (fake client/graph) — RDF 방향·커버리지 회귀."""

from __future__ import annotations

from db_admin.pipeline.delegation_build import build_delegation

_LSSTMD = {"법령체계도": {"상하위법": {"법률": {
    "기본정보": {"법령ID": "001823", "법령명": "건축법", "법종구분": {"content": "법률"}},
    "대통령령": {"기본정보": {"법령ID": "002118", "법령명": "건축법 시행령", "법종구분": {"content": "대통령령"}}},
    "부령1": {"기본정보": {"법령ID": "006191", "법령명": "건축법 시행규칙", "법종구분": {"content": "국토교통부령"}}},
    "부령2": {"기본정보": {"법령ID": "006187", "법령명": "건축물의 구조기준 등에 관한 규칙", "법종구분": {"content": "국토교통부령"}}},
    "자치법규": {"조례": [{"기본정보": {"자치법규ID": "999", "자치법규명": "가평군 건축 조례"}}]},
}}}}


class _FakeClient:
    def resolve_current(self, name): return {"법령ID": "001823", "법령일련번호": "273437"}
    def fetch_hierarchy(self, mst): return _LSSTMD


class _FakeGraph:
    def __init__(self): self.nt = ""; self.graph_uri = None
    def add_nt(self, path, graph_uri=None):
        self.graph_uri = graph_uri
        self.nt = open(path, encoding="utf-8").read()
        return self.nt.count("\n")


def test_build_delegation_covers_all_subordinate():
    g = _FakeGraph()
    rep = build_delegation("건축법", client=_FakeClient(), graph=g)
    # 이름 prefix 무관: 시행령·시행규칙 + '건축물의 구조기준 규칙'(prefix 불일치 부령)도 포함
    assert rep["edges"] == 3
    assert set(rep["children"]) == {"건축법 시행령", "건축법 시행규칙", "건축물의 구조기준 등에 관한 규칙"}


def test_delegatesto_direction_child_to_base():
    g = _FakeGraph()
    build_delegation("건축법", client=_FakeClient(), graph=g)
    # child(002118) --delegatesTo--> base(001823) 방향 확인 (해당 트리플 라인에서)
    line = next(l for l in g.nt.splitlines() if "002118" in l)
    assert "delegatesTo" in line
    assert line.index("002118") < line.index("delegatesTo") < line.rindex("001823")
    assert g.graph_uri.endswith("/delegation/001823")   # base별 named graph
