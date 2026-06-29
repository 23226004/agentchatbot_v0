# conversation-store 백엔드 앱 이미지 (G4 — 검증된 시스템을 배포 가능하게).
# syntax=docker/dockerfile:1
#
# ★ 빌드 컨텍스트 = **repo 루트**(형제 패키지 필요): backend 가 agent·legal_core·legal_infra 를 import.
#   build: docker build -f backend/Dockerfile -t convstore-backend .
#   (compose 는 deploy/docker-compose.app.yml 이 context: .. 로 지정)

# ── 1) builder: venv 에 4 로컬 패키지 비편집 설치(소스를 site-packages 에 baked → 런타임 소스 불요) ──
FROM python:3.12-slim AS builder
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv
ENV UV_LINK_MODE=copy
WORKDIR /build

# 로컬 패키지 소스만 복사(.dockerignore 가 .env·.venv 배제). 의존 그래프: backend→agent→legal_core/legal_infra.
COPY legal_core/  ./legal_core/
COPY legal_infra/ ./legal_infra/
COPY agent/       ./agent/
COPY backend/     ./backend/
# 재현 빌드: 검증된(261 green) 정확 핀을 제약으로 적용 → 전 전이의존이 테스트 통과 버전과 동일(드리프트 방지).
COPY deploy/requirements.lock ./requirements.lock

# 격리 venv + 4 패키지 비편집 설치. agent[observability]=Langfuse extra(env-gate 라 미설정 시 무해).
# 형제 의존은 pyproject 에 선언되지 않으므로(요구사항은 "함께 설치") 네 경로를 명시 — 모두 site-packages 로.
# `-c requirements.lock`: 설치되는 모든 PyPI 의존을 검증 버전으로 핀(미설치분은 강제하지 않음 — pytest 등 제외).
RUN uv venv /opt/venv \
 && VIRTUAL_ENV=/opt/venv uv pip install --no-cache -c ./requirements.lock \
      ./legal_core ./legal_infra "./agent[observability]" ./backend

# ── 2) runtime: venv 만 복사(소스 baked), 비루트, lifespan 이 migrate→setup→reconcile 수행 ──────
FROM python:3.12-slim AS runtime
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PATH="/opt/venv/bin:$PATH"
# 비루트 실행(최소권한)
RUN useradd --create-home --uid 10001 app
COPY --from=builder /opt/venv /opt/venv
# 마이그레이션 SQL 동봉(휠은 패키지 밖 database/migrations 를 포함 못함 → 이미지에 명시 동봉).
# db._migrations_dir() 가 MIGRATIONS_DIR 를 1순위로 읽는다. (교차검증 부팅테스트 적발 수정)
COPY database/migrations/ /app/migrations/
ENV MIGRATIONS_DIR=/app/migrations
USER app
WORKDIR /home/app
EXPOSE 8000

# production factory(--factory): lifespan 이 DB 마이그레이션·setup·reconcile·checkpointer 실주입 후 서빙.
# 호스트/포트/워커·DB·LLM·CORS 등은 런타임 env 로 주입(이미지에 비밀 없음).
CMD ["uvicorn", "backend_app.api.app:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000"]
