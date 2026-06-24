#!/usr/bin/env bash
# 전체 단위/통합 테스트 실행.
# 프로젝트 루트 디렉터리명이 패키지명(legal_core/·legal_infra/)과 같아, 루트가 sys.path에
# 있으면 editable 설치본을 가린다(flat monorepo 섀도잉). → 중립 cwd에서 절대경로로 실행.
# 통합 테스트는 로컬 Qdrant/Fuseki 미가동 시 자동 skip.
set -euo pipefail
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON="${PYTHON:-python}"

cd "$(mktemp -d)"
exec "$PYTHON" -m pytest --import-mode=importlib -q \
  "$REPO/legal_core/tests" \
  "$REPO/legal_infra/tests" \
  "$REPO/db-admin/tests" \
  "$REPO/backend/tests" "$@"
