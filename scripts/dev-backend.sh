#!/usr/bin/env bash
#
# dev-backend.sh — conversation-store 백엔드를 로컬에서 띄우는 **검증된 실행 레시피**(박제).
#
# 왜 이 스크립트가 있나: "지금 되는 실행 방법"을 코드로 박제해 항상 같은 방식으로 뜨게 한다.
# (배포 게이트 B-1: 실행 레시피)  ※ 과거의 PYTHONPATH=agent 함정은 agent 패키지화(agent_app)로 제거됨.
#
# 사용:  bash scripts/dev-backend.sh           # 기본(0.0.0.0:8000, --reload)
#        PORT=9000 RELOAD=0 bash scripts/dev-backend.sh
#
set -euo pipefail

# 이 스크립트(scripts/)의 부모 = repo 루트. 어디서 실행하든 동작.
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENV="$ROOT/agent/.venv"

[ -x "$VENV/bin/uvicorn" ] || { echo "[dev-backend] uvicorn 없음: $VENV (의존성 설치 필요)"; exit 1; }

# ── env 로드 (비밀은 .env 에만 — Zero-Trust). deploy/.env 우선, 없으면 agent/.env ──────────
# .env 는 단순 KEY=value 형식이어야 한다(값에 공백/따옴표 없게).
for ENVF in "$ROOT/deploy/.env" "$ROOT/agent/.env"; do
  if [ -f "$ENVF" ]; then
    set -a; . "$ENVF"; set +a
    echo "[dev-backend] env loaded: ${ENVF/#$ROOT\//}"
    break
  fi
done

# ── 기본값 (미설정 시) ────────────────────────────────────────────────────────────────────
export DATABASE_URL="${DATABASE_URL:-postgresql://convstore:convstore@localhost:5434/convstore}"
export CORS_ORIGINS="${CORS_ORIGINS:-http://localhost:5180}"   # FE(Vite) origin
# ★ 임베딩 토폴로지: 이 프로젝트는 **원격 임베딩 서버**(Tailscale :8081)를 쓴다 — venv 에 torch/FlagEmbedding
#   없음. provider.py 기본은 flag(로컬 torch)라 미설정 시 search_legal 이 ModuleNotFoundError → "일시적 오류"
#   로 마스킹된다. 그래서 remote+RERANK=0(리랭커도 torch) 를 기본으로. 로컬 torch 쓸 땐 EMBEDDING_BACKEND=flag.
export EMBEDDING_BACKEND="${EMBEDDING_BACKEND:-remote}"
export RERANK="${RERANK:-0}"
HOST="${HOST:-0.0.0.0}"; PORT="${PORT:-8000}"; RELOAD="${RELOAD:-1}"

# ── agent_app·backend_app·legal_core·legal_infra 는 모두 editable 설치(`uv pip install -e`)라
#    PYTHONPATH 불요. production factory 의 lazy import `from agent_app.core.agent import ReActAgent`
#    가 바로 풀린다. (과거엔 agent 가 flat `src/` 미설치라 PYTHONPATH=agent 함정이 있었으나 제거됨.)
#    agent_app 미설치 시 1회: `VIRTUAL_ENV=agent/.venv uv pip install -e agent`
"$VENV/bin/python" -c "import agent_app" 2>/dev/null || {
  echo "[dev-backend] agent_app 미설치 → editable 설치"; VIRTUAL_ENV="$VENV" uv pip install -e "$ROOT/agent" >/dev/null; }

# ── LLM 구성 안내(미설정이면 앱이 명확한 ValueError 로 멈춘다 — 여기선 막지 않고 통과) ──────
if [ -z "${LLM_MODEL:-}${LLM_MODELS:-}${GPT_MODEL:-}${GPT_MODELS:-}" ]; then
  echo "[dev-backend] ⚠ LLM 미설정 — .env 에 LLM_MODEL(자체서버) 또는 OPENAI_API+GPT_MODELS(GPT) 필요"
fi

RELOAD_FLAG=()
[ "$RELOAD" = "1" ] && RELOAD_FLAG=(--reload --reload-dir "$ROOT/backend/src" --reload-dir "$ROOT/agent/src")

echo "[dev-backend] http://$HOST:$PORT  CORS=$CORS_ORIGINS  reload=$RELOAD  (DB·LLM 키는 비표시)"
# 빈 배열 확장은 bash 3.2(macOS)의 set -u 에서 'unbound' 오류 → ${arr[@]+"${arr[@]}"} 안전 확장.
exec "$VENV/bin/uvicorn" backend_app.api.app:create_app --factory \
  --host "$HOST" --port "$PORT" ${RELOAD_FLAG[@]+"${RELOAD_FLAG[@]}"}
