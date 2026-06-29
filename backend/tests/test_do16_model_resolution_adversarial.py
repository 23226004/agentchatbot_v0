"""Do-16 런타임 적대 검증 — POST /messages 모델 해석(우선순위·동시성·resume/fork/summarize).

소스 무수정. 기존 test_api.py 패턴(_client·_multi_registry·_agent_with_model·_run_model·
ApprovalAgent·GateAgent) 재사용. 각 가설을 runs.model·run.started{model}·messages.model 로 실측.

실행: PYTHONPATH="backend/src:agent:legal_core/src" agent/.venv/bin/python -m pytest <this> -v
"""
from __future__ import annotations

import threading
import time
import types
import uuid

import pytest

pytest.importorskip("psycopg")
pytest.importorskip("fastapi")

from fastapi.testclient import TestClient  # noqa: E402

from legal_core import ids  # noqa: E402
from legal_core.schemas import AnswerContext, LawRef  # noqa: E402

from backend_app.api import create_app  # noqa: E402
from backend_app.db import build_pool  # noqa: E402

_URI = ids.article_iri("099003", "20260227", 2)
_CID = ids.point_id(_URI)


@pytest.fixture(scope="module", autouse=True)
def _require_pg():
    try:
        p = build_pool(); p.close()
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"convstore postgres 미가동: {exc}")


# ── 스텁(test_api.py 미러) ────────────────────────────────────────────────────
class AIMsg:
    def __init__(self, content="", tool_calls=None):
        self.content = content; self.tool_calls = tool_calls or []


class ToolMessage:
    def __init__(self, tool_call_id, content, artifact=None):
        self.tool_call_id, self.content, self.artifact = tool_call_id, content, artifact


class _Interrupt:
    def __init__(self, value): self.value = value


def _ref():
    return LawRef(id=_CID, kind="law", title="건축법", ref="건축법 제2조",
                  snippet="발췌", url="https://www.law.go.kr/", uri=_URI,
                  resource_id="099003", eff_date="2026-02-27", score=1.0,
                  article_text="제2조 전체 — 거실이란 ...")


def _tool_chunk():
    return {"tools": {"messages": [ToolMessage(
        "c1", "법령 텍스트", artifact=AnswerContext(articles=[_ref()], query="거실"))]}}


def _call_chunk():
    return {"agent": {"messages": [AIMsg(tool_calls=[
        {"id": "c1", "name": "search_legal", "args": {"query": "거실"}}])]}}


def _final_chunk():
    return {"agent": {"messages": [AIMsg(content=f"건축법상 거실은 ...이다 [[cite:{_CID}]].")]}}


_SETTINGS = types.SimpleNamespace(llm_model="qwen3.6-35b-a3b", provider="compatible")


class RunAgent:
    settings = _SETTINGS
    def stream(self, message, thread_id="default"):
        yield _call_chunk(); yield _tool_chunk(); yield _final_chunk()
    def resume(self, thread_id="default"):
        yield from ()
    def reject_pending(self, thread_id="default"):
        pass
    def summarize(self, text):
        return f"[요약] {len(text)}자 대화"
    def fork_state(self, new_id, msgs):
        pass


class ApprovalAgent:
    def stream(self, message, thread_id="default"):
        yield _call_chunk()
        yield {"__interrupt__": (_Interrupt("승인?"),)}
    def resume(self, thread_id="default"):
        yield _tool_chunk(); yield _final_chunk()
    def reject_pending(self, thread_id="default"):
        pass
    def summarize(self, text):
        return f"[요약] {len(text)}자 대화"
    def fork_state(self, new_id, msgs):
        pass


def _client(agent):
    return TestClient(create_app(agent_factory=lambda cp: agent))


def _agent_with_model(mid, provider, base_cls=RunAgent):
    a = base_cls()
    a.settings = types.SimpleNamespace(llm_model=mid, provider=provider)
    return a


def _multi_registry(base_cls=RunAgent):
    return {"gpt-5.4-nano": _agent_with_model("gpt-5.4-nano", "openai", base_cls),
            "qwen3.6-35b-a3b": _agent_with_model("qwen3.6-35b-a3b", "compatible", base_cls)}


def _events(resp):
    return [ln[len("event: "):] for ln in resp.text.splitlines() if ln.startswith("event: ")]


def _wait_status(c, thread_id, statuses, timeout=6.0):
    repo = c.app.state.repo
    deadline = time.time() + timeout
    last = None
    while time.time() < deadline:
        with repo.pool.connection() as conn:
            row = conn.execute("SELECT status::text FROM runs WHERE thread_id=%s "
                               "ORDER BY started_at DESC LIMIT 1", (thread_id,)).fetchone()
        last = row[0] if row else None
        if last in statuses:
            return last
        time.sleep(0.02)
    raise AssertionError(f"run status {last} not in {statuses} within {timeout}s")


def _run_model(c, run_id):
    with c.app.state.repo.pool.connection() as conn:
        return conn.execute("SELECT model FROM runs WHERE id=%s", (run_id,)).fetchone()[0]


def _agent_msg_model(c, tid):
    msgs = c.get(f"/threads/{tid}/messages").json()["messages"]
    am = next((m for m in msgs if m["role"] == "agent"), None)
    return am["model"] if am else None


def _started_model(c, run_id):
    """run.started SSE 이벤트의 model 값."""
    import json
    txt = c.get(f"/runs/{run_id}/stream").text
    for ln in txt.splitlines():
        if ln.startswith("data: "):
            d = json.loads(ln[len("data: "):])
            if "model" in d and "run_id" in d:
                return d["model"]
    return "<no run.started model>"


# ═══════════════════════════════════════════════════════════════════════════
# 가설1: 우선순위 정확성 — body.model → settings.model → 백엔드 기본
# ═══════════════════════════════════════════════════════════════════════════
def test_h1_priority_matrix_full():
    """3계층 우선순위를 runs.model·run.started{model}·messages.model 3축으로 동시 실측."""
    with _client(_multi_registry()) as c:
        # (A) body 명시 → settings 무시
        c.put("/settings", json={"model": "qwen3.6-35b-a3b"})
        tid = c.post("/threads", json={}).json()["id"]
        rid = c.post(f"/threads/{tid}/messages",
                     json={"message": "q", "model": "gpt-5.4-nano"}).json()["run_id"]
        _wait_status(c, tid, ("completed", "error"))
        assert _run_model(c, rid) == "gpt-5.4-nano"
        assert _started_model(c, rid) == "gpt-5.4-nano"
        assert _agent_msg_model(c, tid) == "gpt-5.4-nano"

        # (B) body 없음 → settings 적용
        tid = c.post("/threads", json={}).json()["id"]
        rid = c.post(f"/threads/{tid}/messages", json={"message": "q"}).json()["run_id"]
        _wait_status(c, tid, ("completed", "error"))
        assert _run_model(c, rid) == "qwen3.6-35b-a3b"
        assert _started_model(c, rid) == "qwen3.6-35b-a3b"
        assert _agent_msg_model(c, tid) == "qwen3.6-35b-a3b"

        # (C) settings 무효(레지스트리에 없음) → 백엔드 기본
        c.put("/settings", json={"model": "ghost-model"})
        tid = c.post("/threads", json={}).json()["id"]
        rid = c.post(f"/threads/{tid}/messages", json={"message": "q"}).json()["run_id"]
        _wait_status(c, tid, ("completed", "error"))
        assert _run_model(c, rid) == "gpt-5.4-nano"   # 첫 항목 = default


def test_h1_body_model_null_falls_back_to_settings():
    """body.model=null(JSON) → 미지정 취급 → settings 폴백."""
    with _client(_multi_registry()) as c:
        c.put("/settings", json={"model": "qwen3.6-35b-a3b"})
        tid = c.post("/threads", json={}).json()["id"]
        rid = c.post(f"/threads/{tid}/messages",
                     json={"message": "q", "model": None}).json()["run_id"]
        _wait_status(c, tid, ("completed", "error"))
        assert _run_model(c, rid) == "qwen3.6-35b-a3b"   # null=미지정 → settings


def test_h1_body_model_empty_string_is_422_not_fallback():
    """body.model="" → 명시된 비-None 값. ""은 available 에 없으므로 422(폴백 아님).

    반증 대상: 빈문자열이 '미지정'으로 취급돼 settings 폴백되는가? → None 만 미지정이므로 422 기대.
    """
    with _client(_multi_registry()) as c:
        c.put("/settings", json={"model": "qwen3.6-35b-a3b"})
        tid = c.post("/threads", json={}).json()["id"]
        r = c.post(f"/threads/{tid}/messages", json={"message": "q", "model": ""})
        assert r.status_code == 422, f"빈문자열 model 이 폴백됨(예상 422): {r.status_code}"


def test_h1_body_model_whitespace_is_422():
    """body.model="  "(공백) → trim 안 함, available 에 없음 → 422."""
    with _client(_multi_registry()) as c:
        tid = c.post("/threads", json={}).json()["id"]
        r = c.post(f"/threads/{tid}/messages", json={"message": "q", "model": "  "})
        assert r.status_code == 422


def test_h1_body_model_padded_known_is_422_no_trim():
    """body.model=" gpt-5.4-nano "(패딩) → trim 안 하므로 정확 매칭 실패 → 422.

    관찰: 명시 경로는 공백 정규화를 하지 않는다(엄격 동치). FE 가 패딩을 보내면 거부.
    """
    with _client(_multi_registry()) as c:
        tid = c.post("/threads", json={}).json()["id"]
        r = c.post(f"/threads/{tid}/messages", json={"message": "q", "model": " gpt-5.4-nano "})
        assert r.status_code == 422


# ═══════════════════════════════════════════════════════════════════════════
# 가설2: stale/무효 settings 폴백
# ═══════════════════════════════════════════════════════════════════════════
def test_h2_stale_settings_falls_back_not_422():
    with _client(_multi_registry()) as c:
        c.put("/settings", json={"model": "qwen2.5-7b-mock"})
        tid = c.post("/threads", json={}).json()["id"]
        r = c.post(f"/threads/{tid}/messages", json={"message": "q"})
        assert r.status_code == 200
        _wait_status(c, tid, ("completed", "error"))
        assert _run_model(c, r.json()["run_id"]) == "gpt-5.4-nano"


def test_h2_settings_empty_string_model_falls_back():
    """settings.model="" → isinstance str True, but "" not in available → 기본 폴백."""
    with _client(_multi_registry()) as c:
        c.put("/settings", json={"model": ""})
        tid = c.post("/threads", json={}).json()["id"]
        r = c.post(f"/threads/{tid}/messages", json={"message": "q"})
        assert r.status_code == 200
        _wait_status(c, tid, ("completed", "error"))
        assert _run_model(c, r.json()["run_id"]) == "gpt-5.4-nano"


def test_h2_settings_case_mismatch_falls_back():
    """settings.model 대소문자 불일치 → set 멤버십 실패 → 기본 폴백(엄격 동치, 대소문자 민감)."""
    with _client(_multi_registry()) as c:
        c.put("/settings", json={"model": "GPT-5.4-NANO"})
        tid = c.post("/threads", json={}).json()["id"]
        r = c.post(f"/threads/{tid}/messages", json={"message": "q"})
        assert r.status_code == 200
        _wait_status(c, tid, ("completed", "error"))
        assert _run_model(c, r.json()["run_id"]) == "gpt-5.4-nano"


def test_h2_settings_padded_model_falls_back():
    """settings.model=" gpt-5.4-nano " (공백패딩) → trim 안 함 → 폴백."""
    with _client(_multi_registry()) as c:
        c.put("/settings", json={"model": " gpt-5.4-nano "})
        tid = c.post("/threads", json={}).json()["id"]
        r = c.post(f"/threads/{tid}/messages", json={"message": "q"})
        assert r.status_code == 200
        _wait_status(c, tid, ("completed", "error"))
        assert _run_model(c, r.json()["run_id"]) == "gpt-5.4-nano"


def test_h2_settings_nonstring_model_falls_back():
    """settings.model 이 비문자열로 저장됐다면(이전 PUT) isinstance(str) False → 폴백.

    PUT /settings 가 비문자열을 422 로 막으므로 DB 직접 주입으로 stale 상태를 모사.
    """
    with _client(_multi_registry()) as c:
        # PUT 검증 우회: settings 행을 직접 갱신해 비문자열(숫자) model 주입은 컬럼타입상 불가할 수
        # 있으므로, 컬럼이 text 라면 "123" 으로만 들어간다. 여기선 put 검증이 막는지부터 확인.
        assert c.put("/settings", json={"model": 123}).status_code == 422
        # text 컬럼에 숫자 문자열 "123" 저장 → registry 에 없으니 폴백
        c.put("/settings", json={"model": "123"})
        tid = c.post("/threads", json={}).json()["id"]
        r = c.post(f"/threads/{tid}/messages", json={"message": "q"})
        assert r.status_code == 200
        _wait_status(c, tid, ("completed", "error"))
        assert _run_model(c, r.json()["run_id"]) == "gpt-5.4-nano"


# ═══════════════════════════════════════════════════════════════════════════
# 가설3: resume 모델 일관성 × settings 변경
# ═══════════════════════════════════════════════════════════════════════════
def test_h3_resume_keeps_original_model_despite_settings_change():
    """model=X 로 시작→awaiting→그 사이 PUT settings{Y}→approve → resume 은 원래 X 로 이어가야."""
    with _client(_multi_registry(base_cls=ApprovalAgent)) as c:
        c.put("/settings", json={"model": "qwen3.6-35b-a3b"})   # X (settings)
        tid = c.post("/threads", json={}).json()["id"]
        run_id = c.post(f"/threads/{tid}/messages", json={"message": "q"}).json()["run_id"]
        _wait_status(c, tid, ("awaiting_approval",))
        assert _run_model(c, run_id) == "qwen3.6-35b-a3b"        # X 로 시작
        # 승인 대기 중 settings 를 Y 로 변경
        c.put("/settings", json={"model": "gpt-5.4-nano"})      # Y
        c.post(f"/threads/{tid}/approve", json={"approve": True})
        _wait_status(c, tid, ("completed", "error"))
        # resume 이 원래 X 로 이어가야(settings Y 로 안 바뀜)
        assert _run_model(c, run_id) == "qwen3.6-35b-a3b", "resume 이 settings 변경에 재라우팅됨"
        assert _agent_msg_model(c, tid) == "qwen3.6-35b-a3b"    # 답변 메시지도 X


def test_h3_resume_after_body_model_keeps_it():
    """body.model=X 로 시작(settings=Y)→awaiting→PUT settings{Z}→approve → 여전히 X."""
    with _client(_multi_registry(base_cls=ApprovalAgent)) as c:
        c.put("/settings", json={"model": "gpt-5.4-nano"})       # Y(settings)
        tid = c.post("/threads", json={}).json()["id"]
        run_id = c.post(f"/threads/{tid}/messages",
                        json={"message": "q", "model": "qwen3.6-35b-a3b"}).json()["run_id"]  # X(body)
        _wait_status(c, tid, ("awaiting_approval",))
        c.put("/settings", json={"model": "gpt-5.4-nano"})       # Z
        c.post(f"/threads/{tid}/approve", json={"approve": True})
        _wait_status(c, tid, ("completed", "error"))
        assert _run_model(c, run_id) == "qwen3.6-35b-a3b"        # body.model X 유지


def test_h3_multiturn_settings_switch_per_turn_model():
    """턴1=X(settings)→PUT Y→턴2=Y. 각 답변 메시지의 model 이 그 턴 값과 정합."""
    with _client(_multi_registry()) as c:
        c.put("/settings", json={"model": "qwen3.6-35b-a3b"})    # 턴1 X
        tid = c.post("/threads", json={}).json()["id"]
        c.post(f"/threads/{tid}/messages", json={"message": "q1"})
        _wait_status(c, tid, ("completed", "error"))
        c.put("/settings", json={"model": "gpt-5.4-nano"})       # 턴2 Y
        c.post(f"/threads/{tid}/messages", json={"message": "q2"})
        _wait_status(c, tid, ("completed", "error"))
        msgs = c.get(f"/threads/{tid}/messages").json()["messages"]
        agent_models = [m["model"] for m in msgs if m["role"] == "agent"]
        assert agent_models == ["qwen3.6-35b-a3b", "gpt-5.4-nano"], agent_models


def test_h3_reject_then_new_run_uses_current_settings():
    """reject 후 새 run 은 reject 시점이 아니라 현재 settings 를 따라야(reject 는 모델 고정 안 함)."""
    with _client(_multi_registry(base_cls=ApprovalAgent)) as c:
        c.put("/settings", json={"model": "qwen3.6-35b-a3b"})
        tid = c.post("/threads", json={}).json()["id"]
        c.post(f"/threads/{tid}/messages", json={"message": "q"})
        _wait_status(c, tid, ("awaiting_approval",))
        c.put("/settings", json={"model": "gpt-5.4-nano"})       # reject 전 변경
        c.post(f"/threads/{tid}/approve", json={"approve": False})
        _wait_status(c, tid, ("rejected", "error"))
        # 새 run — 현재 settings(gpt) 적용. ApprovalAgent 는 다시 awaiting 으로 멈추므로
        # awaiting 시점에 runs.model 을 읽어 모델 해석을 검증(완주 불요 — 모델은 open_run 에 즉시 기록).
        rid2 = c.post(f"/threads/{tid}/messages", json={"message": "q2"}).json()["run_id"]
        _wait_status(c, tid, ("awaiting_approval", "completed", "error"))
        assert _run_model(c, rid2) == "gpt-5.4-nano"


# ═══════════════════════════════════════════════════════════════════════════
# 가설4: 동시성
# ═══════════════════════════════════════════════════════════════════════════
def test_h4a_concurrent_put_settings_at_run_start_no_torn_model():
    """run 시작 직전 PUT settings 동시 → 그 run 의 model 은 옛값 XOR 새값(찢김 0)."""
    with _client(_multi_registry()) as c:
        c.put("/settings", json={"model": "qwen3.6-35b-a3b"})
        observed = []
        for _ in range(30):
            tid = c.post("/threads", json={}).json()["id"]
            barrier = threading.Barrier(2)
            results = {}

            def do_put():
                barrier.wait()
                c.put("/settings", json={"model": "gpt-5.4-nano"})

            def do_post():
                barrier.wait()
                results["rid"] = c.post(f"/threads/{tid}/messages",
                                        json={"message": "q"}).json()["run_id"]

            t1 = threading.Thread(target=do_put); t2 = threading.Thread(target=do_post)
            t1.start(); t2.start(); t1.join(); t2.join()
            _wait_status(c, tid, ("completed", "error"))
            m = _run_model(c, results["rid"])
            observed.append(m)
            # 찢김 검출: model 은 두 유효값 중 하나여야(부분문자열·혼합·None 금지)
            assert m in ("qwen3.6-35b-a3b", "gpt-5.4-nano"), f"torn/invalid model: {m!r}"
            # settings 를 다시 옛값으로 리셋해 다음 iteration 레이스 재현
            c.put("/settings", json={"model": "qwen3.6-35b-a3b"})
        # 적어도 한 번은 레이스가 양쪽 결과를 만들었는지(정보 — 단정 아님)
        print("H4a observed distribution:", {v: observed.count(v) for v in set(observed)})


def test_h4b_same_thread_concurrent_post_one_wins_recorded_model_consistent():
    """같은 thread 동시 POST(둘 다 settings 폴백) → 1개만 200, 기록 model=승자값(레이스 엉뚱값 0)."""
    with _client(_multi_registry()) as c:
        c.put("/settings", json={"model": "qwen3.6-35b-a3b"})
        for _ in range(20):
            tid = c.post("/threads", json={}).json()["id"]
            barrier = threading.Barrier(2)
            codes = []
            rids = []
            lock = threading.Lock()

            def fire():
                barrier.wait()
                r = c.post(f"/threads/{tid}/messages", json={"message": "q"})
                with lock:
                    codes.append(r.status_code)
                    if r.status_code == 200:
                        rids.append(r.json()["run_id"])

            ts = [threading.Thread(target=fire) for _ in range(2)]
            for t in ts: t.start()
            for t in ts: t.join()
            _wait_status(c, tid, ("completed", "error"))
            assert sorted(codes) == [200, 409], f"동시 POST 결과 비정상: {codes}"
            assert len(rids) == 1
            assert _run_model(c, rids[0]) == "qwen3.6-35b-a3b"


def test_h4c_multi_thread_post_with_concurrent_settings_flip_each_run_valid():
    """다수 thread 동시 POST + 동시 PUT settings 반복 → 각 run.model 이 유효값(레이스 엉뚱값 0)."""
    with _client(_multi_registry()) as c:
        valid = {"gpt-5.4-nano", "qwen3.6-35b-a3b"}
        c.put("/settings", json={"model": "qwen3.6-35b-a3b"})
        tids = [c.post("/threads", json={}).json()["id"] for _ in range(24)]
        rids = {}
        lock = threading.Lock()
        stop = threading.Event()

        def flipper():
            i = 0
            while not stop.is_set():
                m = "gpt-5.4-nano" if i % 2 else "qwen3.6-35b-a3b"
                c.put("/settings", json={"model": m})
                i += 1

        def poster(tid):
            r = c.post(f"/threads/{tid}/messages", json={"message": "q"})
            if r.status_code == 200:
                with lock:
                    rids[tid] = r.json()["run_id"]

        fl = threading.Thread(target=flipper); fl.start()
        posters = [threading.Thread(target=poster, args=(t,)) for t in tids]
        for t in posters: t.start()
        for t in posters: t.join()
        stop.set(); fl.join()
        for tid, rid in rids.items():
            _wait_status(c, tid, ("completed", "error"))
            m = _run_model(c, rid)
            assert m in valid, f"thread {tid} run {rid} got invalid model {m!r}"


# ═══════════════════════════════════════════════════════════════════════════
# 가설5: get_settings 읽기 비용/경로 — body.model 명시 시 get_settings 회피?
# ═══════════════════════════════════════════════════════════════════════════
def test_h5_body_model_skips_get_settings_query():
    """body.model 명시 시 settings 조회를 하지 않는가? get_settings 호출 카운트로 실측.

    repo.get_settings 를 래핑해 호출수를 센다. POST /messages 1회당:
    - body.model 명시 → settings 조회 0 (불필요 쿼리 회피 기대)
    - body.model 미지정 → settings 조회 1
    """
    with _client(_multi_registry()) as c:
        repo = c.app.state.repo
        orig = repo.get_settings
        calls = {"n": 0}

        def counting(scope="global"):
            calls["n"] += 1
            return orig(scope)
        repo.get_settings = counting

        # (A) body.model 명시 → GET /settings 엔드포인트는 안 부르고 POST 만
        tid = c.post("/threads", json={}).json()["id"]
        calls["n"] = 0
        c.post(f"/threads/{tid}/messages", json={"message": "q", "model": "gpt-5.4-nano"})
        explicit_calls = calls["n"]

        # (B) body.model 미지정 → settings 폴백 경로
        tid2 = c.post("/threads", json={}).json()["id"]
        calls["n"] = 0
        c.post(f"/threads/{tid2}/messages", json={"message": "q"})
        implicit_calls = calls["n"]

        repo.get_settings = orig
        print(f"H5 get_settings calls: explicit={explicit_calls} implicit={implicit_calls}")
        assert explicit_calls == 0, f"body.model 명시인데 settings {explicit_calls}회 조회(불필요 쿼리)"
        assert implicit_calls >= 1, "미지정인데 settings 조회 안 함"


# ═══════════════════════════════════════════════════════════════════════════
# 가설6: fork / summarize 모델
# ═══════════════════════════════════════════════════════════════════════════
def test_h6_fork_child_run_follows_current_settings_not_parent_model():
    """fork child 새 run 의 model 은 settings 따름(fork 자체는 모델 안 옮김)."""
    with _client(_multi_registry()) as c:
        c.put("/settings", json={"model": "qwen3.6-35b-a3b"})
        tid = c.post("/threads", json={}).json()["id"]
        c.post(f"/threads/{tid}/messages", json={"message": "q"})
        _wait_status(c, tid, ("completed", "error"))
        agent_msg = next(m for m in c.get(f"/threads/{tid}/messages").json()["messages"]
                         if m["role"] == "agent")
        assert agent_msg["model"] == "qwen3.6-35b-a3b"
        new_tid = c.post(f"/threads/{tid}/fork",
                         json={"fork_point_message_id": agent_msg["id"]}).json()["thread_id"]
        # fork 후 settings 를 바꾸고 fork thread 에서 새 run
        c.put("/settings", json={"model": "gpt-5.4-nano"})
        rid = c.post(f"/threads/{new_tid}/messages", json={"message": "q2"}).json()["run_id"]
        _wait_status(c, new_tid, ("completed", "error"))
        assert _run_model(c, rid) == "gpt-5.4-nano", "fork child run 이 settings 를 안 따름"


def test_h6_summarize_uses_default_model_regardless_of_settings():
    """summarize 는 RunService.agent(기본 모델)을 쓴다 — settings 영향 없음(설계 확인).

    summarize 는 self.agent.summarize 직접 호출(모델 선택 경로 없음). settings 가 비기본이어도
    기본 agent 가 요약. 결정적 스텁이라 어떤 agent 가 불렸는지 식별 가능하게 모델별 다른 요약 반환.
    """
    # 모델별로 식별 가능한 summarize 결과를 내는 레지스트리
    class Tagged(RunAgent):
        tag = "?"
        def summarize(self, text):
            return f"[{self.tag}] {len(text)}자"

    def tagged(mid, provider, tag):
        a = Tagged()
        a.settings = types.SimpleNamespace(llm_model=mid, provider=provider)
        a.tag = tag
        return a

    reg = {"gpt-5.4-nano": tagged("gpt-5.4-nano", "openai", "DEFAULT"),
           "qwen3.6-35b-a3b": tagged("qwen3.6-35b-a3b", "compatible", "NONDEFAULT")}
    with _client(reg) as c:
        c.put("/settings", json={"model": "qwen3.6-35b-a3b"})   # 비기본 선택
        tid = c.post("/threads", json={}).json()["id"]
        c.post(f"/threads/{tid}/messages", json={"message": "q"})
        _wait_status(c, tid, ("completed", "error"))
        res = c.post(f"/threads/{tid}/summarize", json={}).json()
        # 기본 agent(gpt, 첫 항목)가 요약 → DEFAULT 태그. settings(qwen) 무시.
        assert res["content_md"].startswith("[DEFAULT]"), \
            f"summarize 가 settings 모델을 따름(설계상 기본이어야): {res['content_md']!r}"


# ═══════════════════════════════════════════════════════════════════════════
# 가설7: 검증 경계 재확인
# ═══════════════════════════════════════════════════════════════════════════
def test_h7_body_unknown_422_but_settings_unknown_not_422():
    with _client(_multi_registry()) as c:
        tid = c.post("/threads", json={}).json()["id"]
        # body 명시 unknown → 422
        assert c.post(f"/threads/{tid}/messages",
                      json={"message": "q", "model": "nope"}).status_code == 422
        # settings 경유 unknown → 422 안 남(명시 아님)
        c.put("/settings", json={"model": "nope"})
        tid2 = c.post("/threads", json={}).json()["id"]
        assert c.post(f"/threads/{tid2}/messages", json={"message": "q"}).status_code == 200


def test_h7_available_set_equals_registry():
    """422 메시지의 available 집합이 /models 레지스트리와 정확히 일치."""
    with _client(_multi_registry()) as c:
        models = {m["id"] for m in c.get("/models").json()["models"]}
        tid = c.post("/threads", json={}).json()["id"]
        r = c.post(f"/threads/{tid}/messages", json={"message": "q", "model": "nope"})
        detail = r.json()["detail"]
        # detail 에 sorted(available) 가 들어있음 — 레지스트리와 동일 집합인지
        for mid in models:
            assert mid in detail, f"{mid} 가 available 목록에 없음: {detail}"


def test_h7_404_vs_422_ordering_unknown_model_on_ghost_thread():
    """미존재 thread + unknown body.model: 422(모델검증)가 thread_exists(404)보다 먼저.

    관찰: 모델 검증이 thread_exists 보다 위라 ghost thread 라도 422 가 우선. 순서 결함 여부.
    """
    with _client(_multi_registry()) as c:
        ghost = str(uuid.uuid4())
        r = c.post(f"/threads/{ghost}/messages", json={"message": "q", "model": "nope"})
        # 모델 검증이 먼저면 422, thread 검증이 먼저면 404
        assert r.status_code in (422, 404)
        print(f"H7 ghost+unknown model → {r.status_code} (422=모델검증 우선, 404=thread검증 우선)")
