"""FastAPI 합성 루트 + FE §4.1 핵심 엔드포인트 (conversation-store 슬라이스5).

**합성 루트**: lifespan 이 장수명 풀 1개를 만들고 부팅 시퀀스를 실행한다 —
run_migrations → PostgresSaver.setup() → reconcile_orphan_runs → checkpointer(PostgresSaver)를
agent 에 주입 → RunService 조립. 이로써 슬라이스1~4가 남긴 배포 게이트를 닫는다.

**핵심 엔드포인트**: threads(POST/GET messages=이력복원)·messages(POST→SSE run)·approve(POST→SSE resume).
SSE 는 RunService 제너레이터를 `sse_wire` 로 직렬화해 StreamingResponse 로 흘린다. 동시 run 은 409.
fork/summarize/citations/settings/models 는 후속.
"""

from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager
from typing import Any, Callable

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse

from backend_app.api.run_manager import RunManager
from backend_app.core.container import build_checkpointer, build_pool, run_migrations
from backend_app.repositories import ActiveRunExists, ConversationRepository
from backend_app.services.run_service import RunService, sse_wire

SSE = "text/event-stream"
_log = logging.getLogger("conversation.api")          # G6: API 경로 예외 가시화


def configure_logging() -> None:
    """G6 관측성 — `conversation.*` 네임스페이스 로그를 일관 포맷으로 출력(부팅 시 1회, 멱등).
    설정이 없으면 app 로그(run 시작·에러 진짜 예외)가 안 보이거나 unformatted 였다. `LOG_LEVEL` env(기본
    INFO). root 중복 방지(propagate=False) — uvicorn 의 root 핸들러와 이중출력 안 되게."""
    level = (os.environ.get("LOG_LEVEL", "").strip() or "INFO").upper()
    if level not in logging.getLevelNamesMapping():      # 무효값("GARBAGE"·숫자) → INFO 폴백(부팅크래시 방지)
        level = "INFO"
    logger = logging.getLogger("conversation")
    logger.setLevel(level)
    if not logger.handlers:                              # 멱등(재호출·테스트 재생성서 핸들러 누적 방지)
        h = logging.StreamHandler()
        h.setFormatter(logging.Formatter(
            "%(asctime)s %(levelname)s %(name)s %(message)s"))
        logger.addHandler(h)
        logger.propagate = False


def _default_agent_factory(checkpointer: Any) -> Any:
    """production agent **레지스트리** {model_id: ReActAgent} — 런타임 모델 선택(GPT 다버전/로컬).

    구성된 모든 LLM(Settings.all_from_env: GPT_MODELS·GPT_MODEL·LLM_MODELS·LLM_MODEL)마다 agent 를
    만들되 **checkpointer 는 공유**(대화 history 는 모델 무관 — 모델 바꿔도 같은 thread 이어감). agent
    패키지(agent_app)는 lazy import — editable 설치라 PYTHONPATH 불요. 테스트는 agent_factory 주입으로 우회.

    REQUIRE_APPROVAL=1 이면 도구 실행 전 승인 게이트(interrupt_before) 활성 — 안 켜면 슬라이스4
    승인 흐름(/approve)이 production 에서 도달 불가(awaiting 미발생)였음(교차검증 발견).
    """
    import os  # noqa: PLC0415

    from agent_app.core.agent import ReActAgent  # noqa: PLC0415
    from agent_app.core.config import Settings  # noqa: PLC0415

    require = os.environ.get("REQUIRE_APPROVAL", "0") == "1"
    return {s.llm_model: ReActAgent(settings=s, checkpointer=checkpointer, require_approval=require)
            for s in Settings.all_from_env()}


def create_app(agent_factory: Callable[[Any], Any] | None = None) -> FastAPI:
    """앱 생성. agent_factory(checkpointer)→agent 를 주입하면 그것으로, 없으면 실제 ReActAgent."""
    configure_logging()                                  # G6: app 로그 일관 출력(부팅 1회, 멱등)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        # ── 부팅 시퀀스(배포 게이트) ──────────────────────────────────────────
        pool = build_pool()
        try:                                              # 부팅 중 실패(LLM env 부재 등)에도 풀 정리
            run_migrations(pool)                          # transcript 스키마
            checkpointer = build_checkpointer(pool)       # 영속 checkpointer(1 인스턴스)
            checkpointer.setup()                          # checkpoint* 4테이블(공존, §6 T1)
            repo = ConversationRepository(pool)
            repo.reconcile_orphan_runs()                 # 고아 run 정리(thread 잠금 방지)
            agent = (agent_factory or _default_agent_factory)(checkpointer)
            app.state.pool = pool
            app.state.repo = repo
            app.state.run_service = RunService(agent, repo)
            app.state.runs = RunManager(app.state.run_service)  # run↔stream 디커플
            yield
        finally:
            runs = getattr(app.state, "runs", None)
            if runs is not None:
                runs.shutdown()                           # in-flight pump drain(고아·닫힌풀 접근 방지)
            pool.close()                                  # 부팅 실패·정상종료 모두 풀 닫음(누수 방지)

    app = FastAPI(title="conversation-store", lifespan=lifespan)
    # 미들웨어 순서: add_middleware 는 stack 앞에 삽입(나중 add = 최외곽). **CORS 를 최외곽**으로 두어
    # body-limit 의 413 단락 응답도 CORS 를 거쳐 ACAO 헤더를 받게 한다 — 안 그러면 브라우저가 cross-origin
    # 413 을 차단해 FE 가 opaque "Failed to fetch" 만 보고 사유(413/detail)를 못 본다(XV C 발견).
    app.add_middleware(_BodySizeLimitMiddleware, max_bytes=_max_body_bytes())  # 안쪽
    _add_cors(app)                                                            # 최외곽(나중 add)
    _register_routes(app)
    return app


def _max_body_bytes() -> int:
    """요청 body 상한(env `MAX_REQUEST_BYTES`, 기본 1MB). 필드 캡(message 100k 등)은 **파싱 후**라 거대
    JSON body 자체는 무방비였다(G3 body-DoS) — 이 상한이 파싱 전에 차단한다."""
    import os  # noqa: PLC0415
    raw = os.environ.get("MAX_REQUEST_BYTES", "").strip()
    return int(raw) if raw else 1_048_576


class _BodySizeLimitMiddleware:
    """요청 body 크기 상한 ASGI 미들웨어(G3 DoS). ① Content-Length 헤더로 **파싱 전 413**(표준 클라
    공통 경로). ② 실제 스트림 바이트도 카운트해 초과 시 body 를 끊는다(헤더 누락/위조·chunked 방어 —
    OOM 방지). 요청 측만 검사 → SSE 응답 스트림엔 무영향."""

    def __init__(self, app: Any, max_bytes: int) -> None:
        self.app = app
        self.max_bytes = max_bytes

    async def __call__(self, scope: dict, receive: Any, send: Any) -> None:
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return
        for k, v in scope.get("headers", []):
            if k == b"content-length":
                try:
                    if int(v) > self.max_bytes:
                        await self._reject(send)
                        return
                except ValueError:
                    pass
                break

        total = 0
        stopped = False

        async def limited_receive() -> dict:
            nonlocal total, stopped
            if stopped:
                return {"type": "http.request", "body": b"", "more_body": False}
            msg = await receive()
            if msg.get("type") == "http.request":
                total += len(msg.get("body", b""))
                if total > self.max_bytes:                # 헤더 누락/위조 — 끊어 OOM 방지(라우트 파싱 실패→4xx)
                    stopped = True
                    return {"type": "http.request", "body": b"", "more_body": False}
            return msg

        await self.app(scope, limited_receive, send)

    @staticmethod
    async def _reject(send: Any) -> None:
        import json  # noqa: PLC0415
        body = json.dumps({"detail": "요청 본문이 너무 큽니다"}).encode("utf-8")  # ensure_ascii → ASCII-safe
        await send({"type": "http.response.start", "status": 413,
                    "headers": [(b"content-type", b"application/json"),
                                (b"content-length", str(len(body)).encode())]})
        await send({"type": "http.response.body", "body": body})


def _add_cors(app: FastAPI) -> None:
    """FE(브라우저)가 cross-origin(:5180→:8000)으로 호출하므로 CORS 허용 필수 — 없으면 브라우저가 차단.

    origin 은 `CORS_ORIGINS`(콤마구분) env 로 설정, 기본은 Vite 개발서버(:5180). credentials 를 켜므로
    와일드카드('*')는 스펙상 불가 → **명시 origin 목록**만 허용. SSE(text/event-stream)도 표준 CORS 로 통과.
    """
    import os  # noqa: PLC0415

    raw = os.environ.get("CORS_ORIGINS", "http://localhost:5180").strip()
    origins = [o.strip() for o in raw.split(",") if o.strip()]
    app.add_middleware(
        CORSMiddleware,
        allow_origins=origins,
        allow_credentials=True,
        allow_methods=["*"],          # GET/POST/PUT/OPTIONS(preflight)
        allow_headers=["*"],          # Content-Type, Last-Event-ID 등
        expose_headers=["*"],
    )


def _repo(req: Request) -> ConversationRepository:
    return req.app.state.repo


def _is_uuid(v: Any) -> bool:
    import uuid as _u
    try:
        _u.UUID(str(v))
        return True
    except (ValueError, AttributeError, TypeError):
        return False


# 직접 입력 텍스트의 길이 상한(G3 DoS 표면 차단). 넉넉하되 MB-급 무제한 입력을 막는다.
_MAX_TITLE = 500           # thread 제목 — 짧은 라벨
_MAX_SETTING = 2000        # settings 값(model/theme 짧음, server_url 은 URL 이라 여유)
_MAX_APPROVED = 100        # per-tool 승인 도구 id 리스트 길이(대기 도구는 소수)
_MAX_ID = 200              # tool_call_id 길이(UUID-급)

# 백엔드가 서빙하는 에이전트 프로파일(GET /agents). 현재 법령 ReAct 1종(모델은 GET /models 로 선택).
# 새 분야(예: 건설기준) 추가 시 여기 1행(+해당 도구/프롬프트 백엔드 구현)으로 FE 자동 반영.
_AGENTS = [{"id": "legal", "label": "법률 에이전트", "abbr": "법", "ready": True}]


def _reject_nul(value: Any, field: str, max_len: int | None = None) -> None:
    """저장 텍스트 검증 — 모든 저장-텍스트 경로 공통 가드:
      · NUL(\\x00) 거부 — PG text 불가(안 막으면 DataError→500).
      · **PG 저장 불가 문자(lone surrogate 등) 거부** — NUL 과 동일 클래스(UTF-8 인코딩 실패→500/무성유실).
      · 선택적 **길이 상한**(G3: 무제한 입력 DoS 표면 차단)."""
    if isinstance(value, str):
        if "\x00" in value:
            raise HTTPException(422, f"{field} 에 NUL 문자를 포함할 수 없습니다")
        try:
            value.encode("utf-8")          # lone surrogate 등 인코딩 불가 문자 사전 차단
        except UnicodeEncodeError:
            raise HTTPException(422, f"{field} 에 저장 불가한 문자(surrogate 등)가 있습니다") from None
        if max_len is not None and len(value) > max_len:
            raise HTTPException(422, f"{field} 가 너무 깁니다(최대 {max_len}자)")


def _register_routes(app: FastAPI) -> None:
    @app.get("/stats")
    async def stats(req: Request):
        """G6 운영 메트릭 — run 상태별 수(DB 집계) + 연결풀 통계(active/idle/waiting). 별도 계측 없이
        상태기계·자원 현황 노출. (인증은 G1 — 현재 외부 비노출 전제, 노출 시 auth 게이트 필요.)"""
        pool = req.app.state.pool
        return {"runs": _repo(req).run_status_counts(),
                "pool": pool.get_stats() if hasattr(pool, "get_stats") else {}}

    @app.post("/threads")
    async def create_thread(req: Request, body: dict | None = None):
        body = body or {}
        owner_id, title = body.get("owner_id"), body.get("title")
        if owner_id is not None and not _is_uuid(owner_id):    # 비-UUID owner_id → 500 누수 방지
            raise HTTPException(422, "owner_id 는 UUID 여야 합니다")
        if title is not None and not isinstance(title, str):
            raise HTTPException(422, "title 은 문자열이어야 합니다")
        _reject_nul(title, "title", _MAX_TITLE)
        tid = _repo(req).create_thread(title=title, owner_id=owner_id)
        return {"id": tid}

    @app.get("/threads")
    async def list_threads(req: Request, owner_id: str | None = None):
        # FE 사이드바 스레드 목록(§4.1). owner_id(쿼리) 옵션 — 비-UUID 면 422(500 누수 방지).
        if owner_id is not None and not _is_uuid(owner_id):
            raise HTTPException(422, "owner_id 는 UUID 여야 합니다")
        return {"threads": _repo(req).list_threads(owner_id=owner_id)}

    @app.patch("/threads/{thread_id}")
    async def rename_thread(req: Request, thread_id: str, body: dict | None = None):
        # 대화명 변경(§4.1 사이드바). title 문자열 검증(NUL/surrogate/길이) 후 갱신. 404 없음.
        if not _is_uuid(thread_id):
            raise HTTPException(404, "thread 없음")
        title = (body or {}).get("title")
        if not isinstance(title, str):
            raise HTTPException(422, "title 은 문자열이어야 합니다")
        _reject_nul(title, "title", _MAX_TITLE)
        if not _repo(req).rename_thread(thread_id, title):
            raise HTTPException(404, "thread 없음")
        return {"id": thread_id, "title": title}

    @app.delete("/threads/{thread_id}")
    async def delete_thread(req: Request, thread_id: str):
        # 대화 삭제 — 자식·checkpoint cascade(global citations 보존). 실행중(running) run 있으면 409.
        if not _is_uuid(thread_id):
            raise HTTPException(404, "thread 없음")
        repo = _repo(req)
        if repo.has_running_run(thread_id):
            raise HTTPException(409, "실행 중인 대화는 삭제할 수 없습니다. 완료 후 다시 시도하세요.")
        # fork 후손 보호(교차검증 A1): 참조모델이라 부모 삭제 시 자식 history 가 소실된다 → 분기 먼저 삭제.
        if repo.has_fork_children(thread_id):
            raise HTTPException(409, "이 대화에서 분기된 대화가 있어 삭제할 수 없습니다. 분기를 먼저 삭제하세요.")
        if not repo.delete_thread(thread_id):
            raise HTTPException(404, "thread 없음")
        return {"deleted": True}

    @app.get("/threads/{thread_id}/messages")
    async def get_messages(req: Request, thread_id: str):
        # 이력복원(fork 가시성 포함). content_md/도구셀/seq 순.
        if not _repo(req).thread_exists(thread_id):
            raise HTTPException(404, "thread 없음")
        return {"messages": _repo(req).get_thread_messages(thread_id)}

    @app.post("/threads/{thread_id}/fork")
    async def fork_thread(req: Request, thread_id: str, body: dict):
        # 분기: fork_point 에서 새 thread 생성(참조모델) + checkpoint state 시드(F-1).
        fp = (body or {}).get("fork_point_message_id")
        if not _is_uuid(fp):
            raise HTTPException(422, "fork_point_message_id(UUID) 필요")
        if not _repo(req).thread_exists(thread_id):
            raise HTTPException(404, "thread 없음")
        try:
            new_id = req.app.state.run_service.fork(thread_id, fp)
        except KeyError:
            raise HTTPException(404, "fork_point 가 이 thread 의 메시지가 아님") from None
        return {"thread_id": new_id}

    @app.post("/threads/{thread_id}/summarize")
    async def summarize(req: Request, thread_id: str, body: dict | None = None):
        # 범위 대화 LLM 요약 → summaries 행. {from_seq?, to_seq?}.
        from starlette.concurrency import run_in_threadpool

        if not _repo(req).thread_exists(thread_id):
            raise HTTPException(404, "thread 없음")
        b = body or {}
        from_seq, to_seq = b.get("from_seq"), b.get("to_seq")
        for v in (from_seq, to_seq):
            if v is not None and (not isinstance(v, int) or isinstance(v, bool) or v < 0):
                raise HTTPException(422, "from_seq/to_seq 는 음이 아닌 정수")
        try:
            # **threadpool 오프로드**: 동기 LLM 호출이라 await 없이 부르면 이벤트 루프 전체를
            # 블로킹한다(교차검증 HIGH — run 은 디커플이나 summarize 만 누락). 루프 비점유.
            res = await run_in_threadpool(
                req.app.state.run_service.summarize, thread_id, from_seq, to_seq)
        except Exception:  # noqa: BLE001 — LLM 컨텍스트초과/네트워크 등 → 불투명 500 대신 graceful
            _log.exception("summarize failed thread=%s", thread_id)   # G6: 502 원인 서버 로깅
            raise HTTPException(502, "요약을 생성하지 못했습니다") from None
        if res is None:
            raise HTTPException(422, "요약할 대화가 없습니다")
        return res

    @app.get("/threads/{thread_id}/summaries")
    async def get_summaries(req: Request, thread_id: str):
        if not _repo(req).thread_exists(thread_id):
            raise HTTPException(404, "thread 없음")
        return {"summaries": _repo(req).get_summaries(thread_id)}

    @app.get("/settings")
    async def get_settings(req: Request):
        # scope=global 단일(v1). 없으면 빈 dict.
        return _repo(req).get_settings("global") or {"scope": "global"}

    @app.put("/settings")
    async def put_settings(req: Request, body: dict):
        b = body or {}
        for k in ("model", "server_url", "theme"):
            if b.get(k) is not None and not isinstance(b[k], str):
                raise HTTPException(422, f"{k} 는 문자열이어야 합니다")
            _reject_nul(b.get(k), k, _MAX_SETTING)
        # **부분 갱신**: body 에 있는 키만 전달 → 누락 필드 보존(model 만 PUT 해도 server_url/theme 안 지워짐).
        # 명시 null 은 전달되어 해당 컬럼 비움. (교차검증 HIGH: 전체덮어쓰기가 FE 의 model-만-PUT 에서 데이터손실)
        kw = {k: b[k] for k in ("model", "server_url", "theme") if k in b}
        _repo(req).put_settings("global", **kw)
        return _repo(req).get_settings("global")

    @app.get("/models")
    async def get_models(req: Request):
        # 구성된 모든 LLM 열거(런타임 선택용, GPT 다버전/로컬). FE 가 provider 로 라벨·default 표시.
        svc = req.app.state.run_service
        models = []
        for mid, ag in svc.agents.items():
            st = getattr(ag, "settings", None)
            models.append({"id": mid, "provider": getattr(st, "provider", None),
                           "default": mid == svc.default_model})
        return {"models": models}

    @app.get("/agents")
    async def list_agents():
        # 에이전트 프로파일(표시용) — FE AgentRail 이 seed 대신 동적 로드. 현재 백엔드는 법령 ReAct 에이전트
        # 하나를 서빙(모델은 GET /models 로 별도 선택). FE 가 배열을 {id,label,abbr,ready} 로 매핑.
        return _AGENTS

    @app.get("/threads/{thread_id}/citations")
    async def get_citations(req: Request, thread_id: str):
        # 이력 인용 복원(조상 union·distinct). content_md 의 [[cite:id]] 마커를 이걸로 렌더.
        if not _repo(req).thread_exists(thread_id):
            raise HTTPException(404, "thread 없음")
        return {"citations": _repo(req).get_thread_citations(thread_id)}

    @app.post("/threads/{thread_id}/messages")
    async def post_message(req: Request, thread_id: str, body: dict):
        # 디커플: run 을 백그라운드로 시작하고 run_id 만 반환(설계 §7). 스트림은 GET /runs/{id}/stream.
        message = (body or {}).get("message")
        if not isinstance(message, str) or not message.strip():
            raise HTTPException(422, "message(문자열) 필요")
        _reject_nul(message, "message", 100_000)       # NUL·surrogate·길이(100k) 공통 가드(G3)
        # 모델 선택(런타임): 우선순위 = body.model(명시) → settings.model(FE 가 PUT /settings 로 저장한
        # 선택) → 백엔드 기본. 명시값은 유효성 강제(알 수 없으면 422), 저장값은 유효할 때만 적용(stale/
        # mock 이면 조용히 기본 폴백 — FE 가 이번 요청에 명시한 게 아니므로 422 아님).
        available = set(req.app.state.run_service.agents)
        model = (body or {}).get("model")
        if model is not None:
            if not isinstance(model, str) or model not in available:
                raise HTTPException(422, f"알 수 없는 모델입니다. 가능: {sorted(available)}")
        else:
            saved = (_repo(req).get_settings("global") or {}).get("model")
            if isinstance(saved, str) and saved in available:
                model = saved
        if not _repo(req).thread_exists(thread_id):
            raise HTTPException(404, "thread 없음")
        try:
            run_id = await req.app.state.runs.start(thread_id, message, model)
        except ActiveRunExists:
            raise HTTPException(409, "이 thread 에 진행 중인 run 이 있습니다") from None
        return {"run_id": run_id}

    @app.get("/runs/{run_id}/stream")
    async def stream_run(req: Request, run_id: str):
        # run_id 이벤트 SSE tail(내구 로그 replay+poll). 끊겨도 백그라운드 run 은 완주(고아·오살 없음).
        # **교차 인스턴스·Last-Event-ID**(G4): 어느 인스턴스든 로그를 읽어 서빙, 끊긴 seq 부터 재연결.
        mgr = req.app.state.runs
        if _repo(req).get_run(run_id) is None:        # 비-UUID·미존재 → 404(인프로세스 버퍼 의존 제거)
            raise HTTPException(404, "run 없음")
        # 브라우저 EventSource 는 재연결 시 Last-Event-ID 헤더로 마지막 seq 를 보낸다(쿼리 fallback 도 허용).
        last = req.headers.get("last-event-id") or req.query_params.get("last_event_id")
        try:
            last_seq = int(last) if last is not None else -1
        except ValueError:
            last_seq = -1

        async def body():
            async for ev in mgr.stream(run_id, last_seq):
                yield sse_wire(ev)

        return StreamingResponse(body(), media_type=SSE)

    @app.post("/runs/{run_id}/interrupt")
    async def interrupt_run(req: Request, run_id: str):
        # 실행 중지(§4.1). running=협조취소(다음 청크서 종결), awaiting_approval=즉시 종결.
        if not _is_uuid(run_id):
            raise HTTPException(422, "run_id(UUID) 필요")
        try:
            return await req.app.state.runs.interrupt(run_id)
        except KeyError:
            raise HTTPException(404, "run 없음") from None
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from None

    @app.post("/threads/{thread_id}/approve")
    async def approve(req: Request, thread_id: str, body: dict):
        # 비-UUID thread_id 가드(다른 thread-스코프 라우트와 일관 — 없으면 get_active_run 의
        # uuid 캐스트가 500 누수). thread_exists 가 잘못된 UUID·미존재를 404 로.
        if not _repo(req).thread_exists(thread_id):
            raise HTTPException(404, "thread 없음")
        approve_ = bool((body or {}).get("approve", True))
        # per-tool Stage 2: approved 가 있으면 선택적 실행(그 도구만 실행, 나머지 거절).
        # 없으면 기존 전체 단위(approve bool). 리스트[str] 만 허용.
        approved_ids = (body or {}).get("approved")
        if approved_ids is not None and not (
                isinstance(approved_ids, list) and all(isinstance(x, str) for x in approved_ids)):
            raise HTTPException(422, "approved 는 도구 호출 id 의 문자열 리스트여야 합니다")
        if approved_ids is not None and (
                len(approved_ids) > _MAX_APPROVED or any(len(x) > _MAX_ID for x in approved_ids)):
            raise HTTPException(422, "approved 가 너무 큽니다")   # G3: 무제한 리스트/id DoS 차단
        # 비어있지 않은데 대기 도구와 교집합이 0 → 오타/stale 로 "일부 승인" 의도가 "전량 거절"로
        # 뒤집히는 silent flip 차단(빈 리스트 []=의도적 전량거절은 허용). 대기 run 있을 때만 검증.
        if approved_ids:
            active = _repo(req).get_active_run(thread_id)
            if active is not None and active[1] == "awaiting_approval":
                pending = {t["id"] for t in _repo(req).get_pending_tool_calls(active[0])}
                if not (set(approved_ids) & pending):
                    raise HTTPException(422, "approved 에 유효한 대기 도구 호출 id 가 없습니다")
        try:
            run_id = await req.app.state.runs.resume(
                thread_id, approve=approve_, approved_ids=approved_ids)
        except ValueError:
            raise HTTPException(409, "재개할 승인-대기 run 이 없습니다") from None
        return {"run_id": run_id}