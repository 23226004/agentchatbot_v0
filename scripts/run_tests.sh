#!/usr/bin/env bash
# 전체 단위/통합 테스트 실행.
# 프로젝트 루트 디렉터리명이 패키지명(legal_core/·legal_infra/)과 같아, 루트가 sys.path에
# 있으면 editable 설치본을 가린다(flat monorepo 섀도잉). → 중립 cwd에서 절대경로로 실행.
# 통합/live 테스트는 로컬 Qdrant/Fuseki/Postgres 미가동 시 자동 skip — `-rsxX` 로 skip 사유를
# 항상 출력해 "전부 통과"가 실은 일부 skip 임을 숨기지 않는다(서비스 가동 시에만 전수 실행).
set -euo pipefail
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# PYTHON 기본값 = 공용 venv(uv 관리) python. 이 환경/대다수 개발기엔 PATH 에 `python` 이 없고,
# 5패키지(agent_app/backend_app/legal_core/legal_infra/db_admin)가 이 venv 에 editable 설치돼 있다.
# 다른 머신/CI 는 `PYTHON=/path/to/python bash scripts/run_tests.sh` 로 주입.
PYTHON="${PYTHON:-$REPO/agent/.venv/bin/python}"
[ -x "$PYTHON" ] || { echo "[run_tests] python 실행불가: $PYTHON — PYTHON=<경로> 로 주입하거나 agent/.venv 설치 필요" >&2; exit 1; }

cd "$(mktemp -d)"
exec "$PYTHON" -m pytest --import-mode=importlib -q -rsxX \
  "$REPO/legal_core/tests" \
  "$REPO/legal_infra/tests" \
  "$REPO/db-admin/tests" \
  "$REPO/agent/tests" \
  "$REPO/backend/tests" "$@"
