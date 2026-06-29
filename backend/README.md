# backend

Python 기반 API 및 비즈니스 로직 계층. **SoC**와 **Zero-Trust**를 준수한다.

## 구조 (`src/`)
| 폴더 | 책임 |
|------|------|
| `api/` | 라우터·엔드포인트 (입력 검증, 인증) |
| `core/` | 설정, 보안, 공통 미들웨어 |
| `services/` | 도메인 비즈니스 로직 |
| `repositories/` | DB 접근 추상화 인터페이스 (Vector/Graph 구현 은닉) |
| `schemas/` | 요청·응답 DTO / 검증 스키마 |

## 원칙
- `api`는 `services`만, `services`는 `repositories`만 호출한다.
- DB 구현 교체가 `repositories` 경계 안에서만 일어나도록 한다.

## 로컬 실행 (개발)

```bash
# 전제: PostgreSQL 기동 (없으면 부팅이 즉시 "DB 연결 실패" 로 멈춘다)
docker compose -f deploy/docker-compose.convstore.yml up -d

bash scripts/dev-backend.sh            # 0.0.0.0:8000, --reload (PORT/RELOAD env 로 변경)
```

검증된 실행 레시피를 박제한 스크립트(repo 루트 `scripts/dev-backend.sh`). env 는 `agent/.env`(또는
`deploy/.env`)에서 로드한다. 부팅 = lifespan 이 `build_pool→run_migrations→PostgresSaver.setup→
reconcile→agent 레지스트리(all_from_env)→RunService→RunManager`.

> **패키지화**: `agent`(=`agent_app`)·`backend_app`·`legal_core`·`legal_infra` 모두 editable
> 설치(`uv pip install -e`)라 production factory 의 lazy import `from agent_app.core.agent import
> ReActAgent` 가 PYTHONPATH 없이 바로 풀린다. (과거 flat `src/` 미설치로 PYTHONPATH=agent 가
> 필요하던 함정은 `src→agent_app` 패키지화로 제거됨.) 필수 env: LLM(`LLM_MODEL` 또는
> `OPENAI_API`+`GPT_MODELS`) · `DATABASE_URL`(기본 convstore@5434) · `CORS_ORIGINS`(기본 :5180).
>
> **재현 빌드**: `deploy/requirements.lock`(검증된 230-green PyPI 의존 정확 핀)을 제약으로 설치 —
> `uv pip install -r requirements.txt -c ../deploy/requirements.lock`. (uv 워크스페이스/`uv.lock` 은
> dir명=패키지명 평면 모노레포에서 pytest rootdir 섀도잉으로 테스트를 깨 미채택 — `uv pip freeze` 핀 채택.)
