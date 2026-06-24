"""Fuseki 구현 — GraphRepository.

- add_nt   : GSP PUT (named graph 멱등 교체)
- expand   : VALUES + 술어 화이트리스트, 입력 IRI 검증 + .n3() (Design §9.3, B-1)
- select   : 타입드 바인더(.n3())만. raw 문자열 보간 금지.
"""

from __future__ import annotations

import re

import requests
from rdflib import Literal, URIRef

# RFC 3987 상 IRI에 올 수 없는 문자(공백·꺾쇠·중괄호 등) — 인젝션 차단 (#3)
_ILLEGAL_IRI = re.compile(r"""[\s<>"{}|\\^`]""")

# canonical/채택 네임스페이스 (입력 IRI 검증 화이트리스트)
_ALLOWED_IRI_PREFIXES = (
    "https://2026agent.kr/",
    "http://www.aihub.or.kr/kb/law/",
    "http://www.law.go.kr/",
)
# CURIE prefix → full IRI (expand 술어용)
_PREFIX = {
    "lo": "https://2026agent.kr/ontology#",
    "eli": "http://data.europa.eu/eli/ontology#",
    "law": "http://www.aihub.or.kr/kb/law/",
    "dct": "http://purl.org/dc/terms/",
}


def _validate_iri(iri: str) -> str:
    if not isinstance(iri, str) or not iri.startswith(("http://", "https://")):
        raise ValueError(f"절대 IRI 아님: {iri!r}")
    if _ILLEGAL_IRI.search(iri):                       # #3: 인젝션 문자 차단(계약: ValueError)
        raise ValueError(f"IRI에 허용되지 않은 문자: {iri!r}")
    if not iri.startswith(_ALLOWED_IRI_PREFIXES):
        raise ValueError(f"허용되지 않은 네임스페이스: {iri!r}")
    return iri


def _curie_to_iri(curie: str) -> str:
    if ":" in curie and not curie.startswith("http"):
        pre, _, local = curie.partition(":")
        if pre in _PREFIX:
            iri = _PREFIX[pre] + local
            if _ILLEGAL_IRI.search(iri):               # #3: 술어 인젝션 차단
                raise ValueError(f"술어 IRI 문자 위반: {curie!r}")
            return iri
    raise ValueError(f"알 수 없는 술어 CURIE: {curie!r}")


class FusekiGraph:
    """GraphRepository Protocol 구현."""

    def __init__(self, base_url: str, dataset: str = "law",
                 auth: tuple[str, str] | None = None, timeout: float = 60.0) -> None:
        self.base = base_url.rstrip("/")
        self.dataset = dataset
        self.auth = auth
        self.timeout = timeout

    def add_nt(self, path: str, graph_uri: str | None = None) -> int:
        with open(path, "rb") as fh:
            data = fh.read()
        params = {"graph": graph_uri} if graph_uri else {"default": ""}
        resp = requests.put(
            f"{self.base}/{self.dataset}/data", params=params, data=data,
            headers={"Content-Type": "application/n-triples"},
            auth=self.auth, timeout=self.timeout,
        )
        resp.raise_for_status()
        return sum(1 for ln in data.splitlines() if ln.strip() and not ln.strip().startswith(b"#"))

    def expand(self, uris: list[str], predicates: list[str],
               depth: int = 1, limit: int = 20) -> list[tuple[str, str, str]]:
        if not uris or not predicates:
            return []
        values_n = " ".join(URIRef(_validate_iri(u)).n3() for u in uris)
        values_p = " ".join(URIRef(_curie_to_iri(p)).n3() for p in predicates)
        # depth=1 bounded, 술어 화이트리스트. **양방향**: 노드가 주어(outgoing)거나
        # 목적어(incoming)인 엣지 모두 — delegatesTo는 child→base라, 상위 법령에서 질의해도
        # 하위(시행령/규칙)를 찾으려면 incoming이 필요(법률도메인 검증 반영).
        # default + 모든 named graph 포함.
        q = (
            "SELECT ?s ?p ?o WHERE { "
            f"VALUES ?p {{ {values_p} }} VALUES ?n {{ {values_n} }} "
            "{ { { ?n ?p ?o } UNION { GRAPH ?g1 { ?n ?p ?o } } } BIND(?n AS ?s) } "
            "UNION "
            "{ { { ?s ?p ?n } UNION { GRAPH ?g2 { ?s ?p ?n } } } BIND(?n AS ?o) } "
            f"}} LIMIT {int(limit)}"
        )
        rows = self._query(q)
        # 양방향 결과에서 (s,p,o) 중복 제거
        return list(dict.fromkeys((r["s"], r["p"], r["o"]) for r in rows))

    def select(self, template: str, bindings: dict) -> list[dict]:
        """template 의 $name 토큰을 bindings(URIRef/Literal/str)로 .n3() 치환."""
        q = template
        for name, val in bindings.items():
            term = val if isinstance(val, (URIRef, Literal)) else Literal(val)
            q = q.replace(f"${name}", term.n3())
        return self._query(q)

    def _query(self, sparql: str) -> list[dict]:
        resp = requests.post(
            f"{self.base}/{self.dataset}/query", data={"query": sparql},
            headers={"Accept": "application/sparql-results+json"},
            auth=self.auth, timeout=self.timeout,
        )
        resp.raise_for_status()
        out = []
        for row in resp.json()["results"]["bindings"]:
            out.append({k: v["value"] for k, v in row.items()})
        return out
