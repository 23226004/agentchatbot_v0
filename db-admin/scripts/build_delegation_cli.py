"""위임그래프 빌드 CLI (슬라이스7). lsStmd 기반 delegatesTo 적재.

사용: python db-admin/scripts/build_delegation_cli.py 건축법 [민법 ...]
"""

from __future__ import annotations

import os
import sys

from legal_infra import FusekiGraph

from db_admin.lawgo_client import LawGoClient
from db_admin.pipeline.delegation_build import build_delegation


def main(names: list[str]) -> None:
    client = LawGoClient(oc=os.environ.get("LAW_API_OC", "leehm21897"))
    graph = FusekiGraph(os.environ.get("FUSEKI_URL", "http://localhost:3030"),
                        dataset=os.environ.get("FUSEKI_DATASET", "law"),
                        auth=("admin", os.environ.get("FUSEKI_ADMIN_PASSWORD", "admin123")))
    for name in names:
        rep = build_delegation(name, client=client, graph=graph)
        print(f"[위임] {rep['base']}({rep['base_id']}) delegatesTo 엣지 {rep['edges']}: {rep['children']}")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("usage: python db-admin/scripts/build_delegation_cli.py <법령명> [...]"); sys.exit(1)
    main(sys.argv[1:])
