#!/usr/bin/env bash
# 백엔드 개발 기동 — Postgres(컨테이너) + FastAPI(:8180). agent venv(uv) 사용.
# 사용:  ./run-backend.sh        (FE 는 별 터미널에서  cd frontend && npm run dev)
set -euo pipefail
ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT/agent"

# 0) backend·agent 패키지가 agent venv 에 없으면 1회 editable 설치.
uv run python -c "import backend_app" 2>/dev/null || uv pip install -e ../backend
uv run python -c "import agent_app" 2>/dev/null || uv pip install -e .

# 1) Postgres(:5434) 기동 — 이미 떠 있으면 무시.
docker compose -f ../deploy/docker-compose.convstore.yml up -d

# 2) LLM 키·모델 env 로드(.env: OPENAI_API/GPT_MODEL + LLM_MODEL/LLM_BASE_URL).
set -a; source .env; set +a

# 3) backend 기동(:8180). agent_app 은 editable 설치라 PYTHONPATH 불요(ReActAgent 바로 import).
echo "▶ backend → http://localhost:8180   (모델: GPT=${GPT_MODEL:-none}, 로컬=${LLM_MODEL:-none})"
exec uv run uvicorn backend_app.api.app:create_app --factory --port 8180
