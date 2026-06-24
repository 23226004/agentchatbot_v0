"""law.go.kr OpenAPI HTTP 클라이언트 (db-admin 전용)."""

from __future__ import annotations

import requests

_BASE = "http://www.law.go.kr/DRF"


class LawGoClient:
    def __init__(self, oc: str, timeout: float = 20.0) -> None:
        self.oc = oc
        self.timeout = timeout

    def _get(self, path: str, params: dict) -> dict:
        full = {"OC": self.oc, "type": "JSON", **params}
        r = requests.get(f"{_BASE}/{path}", params=full, timeout=self.timeout)
        r.raise_for_status()
        return r.json()

    def resolve_current(self, name: str) -> dict | None:
        """법령명 정확일치 + 현행 → 유일 법령(MST/법령ID 포함). 모호하면 None(엉뚱연결 방지)."""
        data = self._get("lawSearch.do", {"target": "law", "query": name, "display": 20})
        laws = data.get("LawSearch", {}).get("law", [])
        if isinstance(laws, dict):
            laws = [laws]
        exact = [l for l in laws
                 if l.get("법령명한글") == name and l.get("현행연혁코드") == "현행"]
        return exact[0] if len(exact) == 1 else None

    def fetch_law(self, mst: str) -> dict:
        """lawService.do 본문(JSON)."""
        return self._get("lawService.do", {"target": "law", "MST": str(mst)})

    def fetch_hierarchy(self, mst: str) -> dict:
        """lsStmd 법령체계도(상하위법) JSON — 위임 하위법령 추출용."""
        return self._get("lawService.do", {"target": "lsStmd", "MST": str(mst)})
