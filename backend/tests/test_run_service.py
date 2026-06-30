"""RunService 계약 — agent updates → SSE 8종 · citation.added(artifact) · cite 위조검증.

권위=conversation-store §5. 페이크 agent.stream + 인메모리 FakeRepo 로 서버·LLM·DB 없이
계약을 검증(라이브 PG 영속은 test_run_service_persist.py).
"""

from __future__ import annotations

import pytest

from legal_core.schemas import AnswerContext, LawRef

from backend_app.services.run_service import DISCLAIMER, RunService, sse_wire


class FakeRepo:
    """인메모리 ConversationRepository 대역 — seq 채번·메시지·citation 동결만(계약 검증용)."""

    ACTIVE = ("running", "awaiting_approval", "interrupted")

    def __init__(self):
        self._seq: dict[str, int] = {}
        self.messages: list[dict] = []
        self.citations: dict[str, dict] = {}
        self.runs: dict[str, str] = {}
        self._thread_run: dict[str, str] = {}

    def next_seq(self, thread_id):
        self._seq[thread_id] = self._seq.get(thread_id, 0) + 1
        return self._seq[thread_id]

    def open_run(self, thread_id, model=None):
        rid = f"run-{thread_id}"
        self.runs[rid] = "running"
        self._thread_run[thread_id] = rid
        self.run_model = {**getattr(self, "run_model", {}), rid: model}
        return rid

    def get_run_model(self, run_id):
        return getattr(self, "run_model", {}).get(run_id)

    def get_run(self, run_id):
        st = self.runs.get(run_id)
        return (run_id.removeprefix("run-"), st) if st is not None else None

    def commit_agent_answer(self, thread_id, run_id, *, content_md, citation_ids=None,
                            parent_id=None, model=None):
        if self.runs.get(run_id) != "running":      # CAS: running 일 때만(외부종결이면 None)
            return None
        self.runs[run_id] = "completed"
        return self.add_message(thread_id, role="agent", run_id=run_id, content_md=content_md,
                                citation_ids=citation_ids, parent_id=parent_id, model=model)

    def set_run_status(self, run_id, status, *, ended=False):
        self.runs[run_id] = status

    def try_transition(self, run_id, from_status, to_status, *, ended=False):
        if self.runs.get(run_id) == from_status:
            self.runs[run_id] = to_status
            return True
        return False

    def get_active_run(self, thread_id):
        rid = self._thread_run.get(thread_id)
        if rid and self.runs.get(rid) in self.ACTIVE:
            return (rid, self.runs[rid])
        return None

    def set_pending_approval(self, run_id, state):
        for m in self.messages:
            if m.get("run_id") == run_id and m["role"] == "tool" and m["tool_result"] is None:
                m["approval_state"] = state

    def get_pending_tool_calls(self, run_id):
        return [{"id": m["tool_call_id"], "name": m["tool_name"], "args": m["tool_args"]}
                for m in self.messages
                if m.get("run_id") == run_id and m["role"] == "tool" and m["tool_result"] is None]

    def add_message(self, thread_id, *, role, run_id=None, seq=None, content_md=None,
                    tool_name=None, tool_args=None, tool_result=None, approval_state=None,
                    parent_id=None, citation_ids=None, tool_call_id=None, model=None):
        if seq is None:
            seq = self.next_seq(thread_id)
        mid = f"msg-{len(self.messages)}"
        self.messages.append({"id": mid, "role": role, "seq": seq, "content_md": content_md,
                              "tool_name": tool_name, "tool_args": tool_args, "tool_result": tool_result,
                              "run_id": run_id, "tool_call_id": tool_call_id,
                              "approval_state": approval_state, "model": model,
                              "citation_ids": list(citation_ids or [])})
        return mid, seq

    def set_thread_title_if_empty(self, thread_id, title):
        # 첫 질문 자동제목(WHERE title IS NULL 모사) — run 거동 테스트라 추적만, IS NULL 의미만 호환.
        if not hasattr(self, "titles"):
            self.titles = {}
        if self.titles.get(thread_id) is None:
            self.titles[thread_id] = title
            return True
        return False

    def set_tool_result(self, run_id, tool_call_id, tool_result):
        for m in self.messages:
            if m.get("run_id") == run_id and m.get("tool_call_id") == tool_call_id:
                m["tool_result"] = tool_result

    def resolve_orphan_tool_cells(self, run_id, marker):
        for m in self.messages:
            if m.get("run_id") == run_id and m["role"] == "tool" and m["tool_result"] is None:
                m["tool_result"] = marker

    def freeze_citation(self, citation):
        self.citations.setdefault(citation["id"], citation)  # ON CONFLICT DO NOTHING

    def get_thread_citations(self, thread_id):
        # 대역: 동결된 citation 전부(실 repo 는 thread ancestor-union 으로 스코프). 위조검증 valid 용.
        return [{"id": cid} for cid in self.citations]

    def get_turn_root(self, run_id):
        return next((m["id"] for m in self.messages
                     if m.get("run_id") == run_id and m["role"] == "user"), None)


# --- 페이크 메시지(덕타이핑: 클래스명/속성만 맞추면 됨) ---
class AIMsg:
    def __init__(self, content="", tool_calls=None):
        self.content = content
        self.tool_calls = tool_calls or []


class ToolMessage:  # 클래스명이 RunService 의 판별 기준
    def __init__(self, tool_call_id, content, artifact=None):
        self.tool_call_id = tool_call_id
        self.content = content
        self.artifact = artifact


def _ref(rid, no):
    return LawRef(id=rid, kind="law", title="건축법", ref=f"건축법 {no}",
                  snippet="발췌", url="https://www.law.go.kr/", uri=f"u-{rid}",
                  resource_id="001823", eff_date="2026-02-27", score=1.0,
                  article_text=f"{no} 전체")


class FakeAgent:
    """canned updates 스트림. body 를 바꿔 위조 케이스도 만든다."""

    def __init__(self, body):
        self._body = body

    def stream(self, message, thread_id="default"):
        yield {"agent": {"messages": [AIMsg(tool_calls=[
            {"id": "c1", "name": "search_legal", "args": {"query": "거실"}}])]}}
        yield {"tools": {"messages": [ToolMessage(
            "c1", "법령 텍스트",
            artifact=AnswerContext(articles=[_ref("ID1", "제2조"), _ref("ID2", "제53조")],
                                   query="거실"))]}}
        yield {"agent": {"messages": [AIMsg(content=self._body)]}}


def _events(body, repo=None):
    return list(RunService(FakeAgent(body), repo or FakeRepo()).run("거실이 뭐야?", thread_id="t1"))


def test_emits_eight_event_contract_in_order():
    evs = _events("건축법상 거실은 ...을 말한다 [[cite:ID1]].")
    names = [e.event for e in evs]
    assert names == [
        "run.started", "tool.call", "tool.result",
        "citation.added", "citation.added", "message.completed", "run.done",
    ]
    # seq 는 단조 증가·유일(관찰 순서 보장). 이벤트 없는 메시지(user)도 seq 를 소비하므로
    # 이벤트 seq 는 연속이 아닐 수 있다(thread 전역 카운터).
    seqs = [e.seq for e in evs]
    assert seqs == sorted(seqs) and len(set(seqs)) == len(seqs)


def test_persists_transcript_via_repo():
    """슬라이스3: 이벤트가 repo 로 영속되는지(run status·도구셀·citation 동결·답변+링크)."""
    repo = FakeRepo()
    _events("건축법상 거실은 ...을 말한다 [[cite:ID1]].", repo=repo)
    assert repo.runs == {"run-t1": "completed"}                       # run 종결
    assert set(repo.citations) == {"ID1", "ID2"}                      # 2건 전역 동결(전문 포함)
    assert "전체" in repo.citations["ID1"]["article_text"]            # snippet 아닌 전문 freeze
    tool_msgs = [m for m in repo.messages if m["role"] == "tool"]
    assert len(tool_msgs) == 1 and tool_msgs[0]["tool_name"] == "search_legal"
    assert tool_msgs[0]["tool_result"] == "법령 텍스트"               # tool.result 갱신됨
    agent_msgs = [m for m in repo.messages if m["role"] == "agent"]
    assert len(agent_msgs) == 1
    assert agent_msgs[0]["citation_ids"] == ["ID1"]                   # 본문 인용만 링크(전체 아님)
    assert DISCLAIMER in agent_msgs[0]["content_md"]                  # 최종본문(면책) 영속


def test_citation_added_from_artifact():
    evs = _events("... [[cite:ID1]].")
    cites = [e for e in evs if e.event == "citation.added"]
    assert {c.data["id"] for c in cites} == {"ID1", "ID2"}
    c = cites[0].data
    assert c["kind"] == "law" and c["title"] == "건축법" and "url" in c


def test_tool_result_truncated_for_display_g3():
    """G3 크기상한: 거대 도구결과(article_text 전문 SSE 정보과다)는 표시값(SSE·저장)만 상한으로 자른다.
    LLM 컨텍스트(checkpoint)·citation 전문은 별도라 무영향(여기선 표시값 truncate 만 검증)."""
    class BigToolAgent:
        def stream(self, message, thread_id="default"):
            yield {"agent": {"messages": [AIMsg(tool_calls=[
                {"id": "c1", "name": "search_legal", "args": {"query": "거실"}}])]}}
            yield {"tools": {"messages": [ToolMessage("c1", "법" * 20000)]}}   # 40000자 거대
            yield {"agent": {"messages": [AIMsg(content="답변")]}}
    repo = FakeRepo()
    evs = list(RunService(BigToolAgent(), repo).run("q", thread_id="t1"))
    tr = next(e for e in evs if e.event == "tool.result")
    assert len(tr.data["content"]) < 20000 and "표시 생략" in tr.data["content"]   # SSE 상한
    cell = next(m for m in repo.messages if m["role"] == "tool")
    assert "표시 생략" in cell["tool_result"]                                      # 저장값도 상한


def test_citation_added_dedups_same_id_within_artifact():
    """한 artifact 가 동일 조문(id) 2건을 담아도 citation.added 는 1건(FE 중복 누출 방지)."""

    class DupAgent:
        def stream(self, message, thread_id="default"):
            yield {"tools": {"messages": [ToolMessage(
                "c1", "t",
                artifact=AnswerContext(
                    articles=[_ref("ID1", "제2조"), _ref("ID1", "제2조")], query="q"))]}}
            yield {"agent": {"messages": [AIMsg(content="... [[cite:ID1]].")]}}

    evs = list(RunService(DupAgent(), FakeRepo()).run("q", thread_id="t1"))
    cites = [e for e in evs if e.event == "citation.added"]
    assert len(cites) == 1 and cites[0].data["id"] == "ID1"


def test_message_completed_has_disclaimer_version_and_used_citations():
    evs = _events("건축법상 거실은 ...을 말한다 [[cite:ID1]].")
    done = next(e for e in evs if e.event == "message.completed")
    assert done.data["content_type"] == "markdown"
    assert done.data["citations"] == ["ID1"]          # 본문에 실제 인용된 것만
    assert DISCLAIMER in done.data["text"]             # 면책 강제(프롬프트 비의존)
    assert "기준 시행일자: 2026-02-27" in done.data["text"]  # 버전 고지


def test_whitespace_padded_cite_marker_not_falsely_forged():
    """교차검증: LLM 이 `[[cite: ID1 ]]`(공백패딩)을 써도 정당 인용이 cite_forgery 로 오탐되지 않아야.

    공백째 캡처되면 frozen id(ID1)와 불일치 → 답변 전체가 error 로 막혔다. 본문 정규화로 구제.
    """
    evs = _events("건축법상 거실은 ...이다 [[cite: ID1 ]].")
    assert not any(e.event == "error" for e in evs)         # 위조 오탐 없음
    done = next(e for e in evs if e.event == "message.completed")
    assert done.data["citations"] == ["ID1"]                # 정규화된 id 로 링크
    assert "[[cite:ID1]]" in done.data["text"]              # 저장 본문도 무공백(FE 렌더 정합)


def test_uppercase_cite_marker_is_matched_not_bypassed():
    """교차검증: 대문자 `[[CITE:id]]` 도 위조검증·정규화 대상(IGNORECASE) — 우회/무인용 저장 방지."""
    evs = _events("건축법상 거실은 ...이다 [[CITE:ID1]].")
    assert not any(e.event == "error" for e in evs)         # 정당 인용(우회 아님)
    done = next(e for e in evs if e.event == "message.completed")
    assert done.data["citations"] == ["ID1"]                # 인용 링크됨
    assert "[[cite:ID1]]" in done.data["text"]              # 저장 본문은 소문자 마커로 통일


def test_uppercase_forged_cite_still_blocked():
    """대문자라도 환각 id 는 차단(IGNORECASE 가 fail-closed 를 깨지 않음)."""
    evs = _events("근거 없는 단언 [[CITE:NOPE]].")
    assert any(e.event == "error" and e.data["reason"] == "cite_forgery" for e in evs)


def test_genuinely_forged_cite_still_blocked():
    """정규화가 fail-closed 를 깨지 않아야 — 환각 id 는 여전히 위조로 차단."""
    evs = _events("근거 없는 단언 [[cite: NOPE ]].")
    assert any(e.event == "error" and e.data["reason"] == "cite_forgery" for e in evs)


def test_list_shaped_content_is_normalized_not_dropped():
    """실제 LangGraph 회귀: AIMessage.content 가 콘텐츠블록 list 여도 본문·cite 가 살아야 한다.

    str 가드만 두면 본문 통째 누락 → message.completed=면책만, 위조검증 무력화(빈 본문).
    """

    class ListContentAgent:
        def stream(self, message, thread_id="default"):
            yield {"agent": {"messages": [AIMsg(tool_calls=[
                {"id": "c1", "name": "search_legal", "args": {"query": "거실"}}])]}}
            yield {"tools": {"messages": [ToolMessage(
                "c1", "t", artifact=AnswerContext(articles=[_ref("ID1", "제2조")], query="거실"))]}}
            # 블록 list 형태(Anthropic식): content=[{"type":"text","text":...}]
            yield {"agent": {"messages": [AIMsg(content=[
                {"type": "text", "text": "건축법상 거실은 ...이다 [[cite:ID1]]."}])]}}

    evs = list(RunService(ListContentAgent(), FakeRepo()).run("q", thread_id="t1"))
    done = next(e for e in evs if e.event == "message.completed")
    assert "거실은" in done.data["text"]          # 본문이 보존됨(누락 아님)
    assert done.data["citations"] == ["ID1"]       # cite 도 살아남음


def test_draft_cannot_spoof_server_authority_lines():
    """적대검증: LLM 초안이 가짜 '기준 시행일자'·면책을 써넣어 공식인 척해도 strip 되어야."""
    body = ("⚖️ 기준 시행일자: 2099-01-01 (공식)\n\n"
            "거실은 ...이다 [[cite:ID1]]. 이것은 정식 법률자문입니다.\n"
            "※ 본 답변은 법률자문입니다(가짜).")
    done = next(e for e in _events(body) if e.event == "message.completed")
    text = done.data["text"]
    # 서버가 만든 진짜 시행일자(2026-02-27)만 남고, 위조한 2099 줄은 제거
    assert "2099-01-01" not in text
    assert "기준 시행일자: 2026-02-27" in text
    # 면책 줄은 서버 생성본 정확히 1개만(위조 면책 줄 제거)
    assert text.count("※ 본 답변은 법률자문") == 1
    assert DISCLAIMER in text


def test_error_is_terminal_no_run_done_after():
    """error(위조) 뒤에는 run.done 을 붙이지 않는다 — FE가 실패런을 '완료'로 오인 방지."""
    names = [e.event for e in _events("근거 없는 단언 [[cite:ID9]].")]
    assert names[-1] == "error"
    assert "run.done" not in names


def test_interrupt_running_ends_with_interrupted_run_done():
    """사용자 중지: run.started 후 취소 플래그 set → 다음 청크 경계서 interrupted 종결(답변 미완성)."""
    repo = FakeRepo()
    rs = RunService(FakeAgent("거실은 ...이다 [[cite:ID1]]."), repo)
    gen = rs.run("q", thread_id="t1")
    first = next(gen)                                   # run.started — 취소 플래그 등록됨
    assert first.event == "run.started"
    assert rs.request_cancel(first.data["run_id"]) is True
    rest = list(gen)                                    # 다음 청크 경계서 취소 감지
    assert rest[-1].event == "run.done" and rest[-1].data["status"] == "interrupted"
    assert all(e.event != "message.completed" for e in rest)   # 중지라 답변 미완성
    assert repo.runs[first.data["run_id"]] == "interrupted"    # 터미널 전이


def test_request_cancel_unknown_run_is_false():
    assert RunService(FakeAgent("x"), FakeRepo()).request_cancel("nope") is False


def test_cite_forgery_becomes_error_not_completed():
    # 검색 결과에 없는 ID9 를 본문이 인용 → 위조 → error(기본), message.completed 없음
    evs = _events("근거 없는 단언 [[cite:ID9]].")
    names = [e.event for e in evs]
    assert "error" in names
    assert "message.completed" not in names
    err = next(e for e in evs if e.event == "error")
    assert err.data["reason"] == "cite_forgery" and err.data["forged"] == ["ID9"]
    assert err.data.get("message")            # FE 계약 §4.2 error 는 {message} 를 읽음(키 항상 존재)


def test_approval_requested_carries_fe_contract_keys():
    """FE 계약 §4.2 승인모달 {id, action, detail} — backend approval.requested payload 정합."""
    rs = RunService(ApprovalAgent(), FakeRepo())
    evs = list(rs.run("q", thread_id="t1"))
    req = next(e for e in evs if e.event == "approval.requested")
    assert req.data["id"] == req.data["run_id"]      # id = run_id alias
    assert req.data["action"] == "tool" and "detail" in req.data


def test_sse_wire_serialization():
    evs = _events("... [[cite:ID1]].")
    wire = sse_wire(evs[0])
    # id: seq(Last-Event-ID 추적, G4) → event → data 순.
    assert wire.startswith(f"id: {evs[0].seq}\nevent: run.started\ndata: ")
    assert '"seq": 1' in wire and wire.endswith("\n\n")


# ── 슬라이스4: 승인 interrupt + 재개 ──────────────────────────────────────────
class _Interrupt:
    def __init__(self, value):
        self.value = value


class ApprovalAgent:
    """첫 stream 은 도구호출 후 interrupt(승인대기), resume 은 도구실행→최종답변."""

    def stream(self, message, thread_id="default"):
        yield {"agent": {"messages": [AIMsg(tool_calls=[
            {"id": "c1", "name": "search_legal", "args": {"query": "거실"}}])]}}
        yield {"__interrupt__": (_Interrupt("search_legal 실행을 승인하시겠습니까?"),)}

    def resume(self, thread_id="default"):
        yield {"tools": {"messages": [ToolMessage(
            "c1", "법령 텍스트",
            artifact=AnswerContext(articles=[_ref("ID1", "제2조")], query="거실"))]}}
        yield {"agent": {"messages": [AIMsg(content="거실은 ...이다 [[cite:ID1]].")]}}


def test_interrupt_pauses_and_persists_awaiting():
    repo = FakeRepo()
    evs = list(RunService(ApprovalAgent(), repo).run("q", thread_id="t1"))
    assert [e.event for e in evs] == ["run.started", "tool.call", "approval.requested"]
    assert evs[-1].data["detail"] == "search_legal 실행을 승인하시겠습니까?"   # interrupt value 전달
    assert repo.runs["run-t1"] == "awaiting_approval"                          # status 영속
    tool = next(m for m in repo.messages if m["role"] == "tool")
    assert tool["approval_state"] == "pending" and tool["tool_result"] is None  # 결과 미도착


def test_approve_resumes_to_completion():
    repo = FakeRepo()
    rs = RunService(ApprovalAgent(), repo)
    list(rs.run("q", thread_id="t1"))                                          # 일시정지
    evs = list(rs.resume("t1", approve=True))                                  # 승인 재개
    assert [e.event for e in evs] == ["tool.result", "citation.added",
                                      "message.completed", "run.done"]
    assert repo.runs["run-t1"] == "completed"
    tool = next(m for m in repo.messages if m["role"] == "tool")
    assert tool["tool_result"] == "법령 텍스트"                                # (run_id,tool_call_id)로 갱신
    assert tool["approval_state"] == "approved"
    agent_msg = next(m for m in repo.messages if m["role"] == "agent")
    assert agent_msg["citation_ids"] == ["ID1"] and DISCLAIMER in agent_msg["content_md"]


def test_reject_terminates_as_rejected():
    repo = FakeRepo()
    rs = RunService(ApprovalAgent(), repo)
    list(rs.run("q", thread_id="t1"))
    evs = list(rs.resume("t1", approve=False))
    assert [e.event for e in evs] == ["run.done"]
    assert evs[0].data["status"] == "rejected" and repo.runs["run-t1"] == "rejected"
    assert next(m for m in repo.messages if m["role"] == "tool")["approval_state"] == "rejected"


def test_resume_without_awaiting_run_raises():
    with pytest.raises(ValueError):
        list(RunService(ApprovalAgent(), FakeRepo()).resume("nope", approve=True))


def test_generatorexit_marks_running_run_error():
    """제너레이터 명시 close(GeneratorExit) 시 running run 을 error 로 종결(best-effort 정리).

    GeneratorExit 는 BaseException 이라 except Exception 을 우회 → 별도 처리 없으면 'running' 잔류.
    **한계**(실증): 블로킹 agent 스트림 도중 실제 uvicorn 끊김은 gen 이 즉시 close 안 돼 이 경로가
    안 탈 수 있음 → 고아는 timeout_stale_runs 가 회수. 여기선 명시 close 경로만 검증.
    """
    repo = FakeRepo()
    gen = RunService(FakeAgent("답 [[cite:ID1]]."), repo).run("q", thread_id="t1")
    next(gen)                                    # run.started → open_run(running)
    next(gen)                                    # tool.call (스트림 도중)
    gen.close()                                  # 끊김 → GeneratorExit 주입
    assert repo.runs["run-t1"] == "error"        # 종결됨(고아 아님)


def test_disconnect_preserves_awaiting_approval():
    """일시정지(awaiting_approval) 중 종료는 CAS 불일치로 보존(durable, error 로 안 죽임)."""
    repo = FakeRepo()
    rs = RunService(ApprovalAgent(), repo)
    list(rs.run("q", thread_id="t1"))            # approval.requested 까지 → awaiting_approval
    assert repo.runs["run-t1"] == "awaiting_approval"   # 보존(끊겨도 error 아님)


def test_terminal_cas_no_resurrection():
    """sweep/reconcile 가 run 을 error 로 쓴 뒤엔 _drive 가 completed 로 되살리지 않는다(부활 차단).

    무가드 set_run_status 면 error→completed 부활+active-run 인덱스 풀려 이중 run 가능했음(교차검증).
    """
    repo = FakeRepo()
    gen = RunService(FakeAgent("답 [[cite:ID1]]."), repo).run("q", thread_id="t1")
    names = []
    for ev in gen:
        names.append(ev.event)
        if ev.event == "tool.result":              # 외부 sweep 이 run **진행 중** error 로 종결
            repo.runs["run-t1"] = "error"
    # 답변 영속+완료 CAS 가 원자(commit_agent_answer) → CAS 패배(이미 error)면 영속·방출 전무
    assert "message.completed" not in names        # orphan 답변 0(원자 게이트)
    assert "run.done" not in names                 # 종결 CAS 실패 → run.done 억제
    assert repo.runs["run-t1"] == "error"          # 되살아나지 않음(부활 차단)


def test_recite_prior_thread_citation_is_not_forgery():
    """멀티턴(교차검증 HIGH): 이전 턴에 동결된 citation을 도구 재호출 없이 재인용 → 위조 아님.

    위조검증이 per-run frozen 만 보면 멀티턴 재인용을 오탐(error)했음 → thread 전역 frozen 으로.
    """
    repo = FakeRepo()
    repo.freeze_citation({"id": "ID1", "kind": "law", "title": "건축법"})  # 이전 턴 동결분

    class ReciteAgent:                                  # 도구 호출 없이 이전 근거 재인용
        def stream(self, message, thread_id="default"):
            yield {"agent": {"messages": [AIMsg(content="앞서 본 근거대로 ...이다 [[cite:ID1]].")]}}

    names = [e.event for e in RunService(ReciteAgent(), repo).run("후속질문", thread_id="t1")]
    assert "message.completed" in names and "error" not in names   # 재인용은 정당


def test_user_message_persist_failure_terminates_run_cleanly():
    """user 메시지 영속 실패(예: NUL DataError)가 running 고아를 안 남기고 error 로 정리(교차검증 회귀)."""

    class RaisingRepo(FakeRepo):
        def add_message(self, thread_id, *, role, **kw):
            if role == "user":
                raise ValueError("NUL")               # PG DataError 모사
            return super().add_message(thread_id, role=role, **kw)

    repo = RaisingRepo()
    evs = list(RunService(FakeAgent("x"), repo).run("q", thread_id="t1"))
    assert [e.event for e in evs] == ["run.started", "error"]   # _drive 진입 안 하고 정리
    assert repo.runs["run-t1"] == "error"                       # running 고아 아님(thread 잠금 방지)


def test_error_path_logs_real_exception_g6():
    """G6: 사용자엔 일반 메시지(내부정보 0)지만, **운영자에겐 진짜 예외가 서버 로그에 남는다**(run 이
    왜 죽었는지 진단 가능). 안 그러면 error 의 원인이 영영 불명이었다."""
    import io
    import logging

    class RaisingRepo(FakeRepo):
        def add_message(self, thread_id, *, role, **kw):
            if role == "user":
                raise ValueError("BOOM_secret_detail")   # 내부 예외(검색다운/PG/버그 모사)
            return super().add_message(thread_id, role=role, **kw)

    logger = logging.getLogger("conversation.run")
    buf = io.StringIO()
    h = logging.StreamHandler(buf)                        # 직접 캡처(propagate 무관)
    logger.addHandler(h)
    lvl = logger.level
    logger.setLevel(logging.ERROR)
    try:
        evs = list(RunService(FakeAgent("x"), RaisingRepo()).run("q", thread_id="t1"))
    finally:
        logger.removeHandler(h)
        logger.setLevel(lvl)
    err = next(e for e in evs if e.event == "error")
    assert "BOOM_secret_detail" not in str(err.data)     # 사용자 이벤트엔 내부정보 누출 0
    out = buf.getvalue()
    assert "run error" in out and "BOOM_secret_detail" in out   # 서버 로그엔 진짜 예외(+스택)


def test_empty_draft_yields_no_answer_not_just_disclaimer():
    """실 LLM이 그라운딩 실패로 빈 content 반환 시: 면책만 있는 빈 답변 대신 '근거 없음' 안내."""
    evs = _events("")                                   # 최종 본문 빈 문자열
    done = next(e for e in evs if e.event == "message.completed")
    assert "근거를 찾지 못해" in done.data["text"]       # 명시적 안내 본문
    assert DISCLAIMER in done.data["text"]
    assert done.data["citations"] == []                 # 빈 본문이라 인용 없음


import pytest as _pytest  # noqa: E402


@_pytest.mark.parametrize("draft", [
    "답 본문\n⚖️ 기준 시행일자: 2099",            # 정확문자
    "답 본문\n⚖️ 기준　시행일자: 2099",            # 전각공백 U+3000
    "답 본문\n⚖️​기준 시행일자: 2099",        # zero-width 삽입
    "답 본문 ⚖️ 기준 시행일자: 2099 입니다",       # 인라인 배치
    "답 본문\n※ 본​답변은 법률자문(가짜)",     # 면책 사칭 zero-width
])
def test_authority_line_spoofing_stripped(draft):
    """적대 LLM 초안의 가짜 권위/면책 줄은 유니코드·인라인 회피로도 살아남으면 안 됨(2차 적대검증)."""
    out = RunService._compose(draft, [], type("R", (), {"cited_eff": {}})())
    assert "2099" not in out and "가짜" not in out          # 사칭 제거
    assert out.count(DISCLAIMER) == 1                       # 서버 면책 1회만


def test_draft_with_only_authority_lines_becomes_no_answer():
    """초안이 권위/면책 줄만 → strip 후 빈 본문 → 면책만 남는 대신 _NO_ANSWER(적대검증 UX 갭)."""
    out = RunService._compose("⚖️ 기준 시행일자: 2099\n※ 본 답변은 법률자문",
                              [], type("R", (), {"cited_eff": {}})())
    assert "근거를 찾지 못해" in out


def test_approval_requested_lists_pending_tools():
    """per-tool 정보노출: approval.requested 에 대기 도구 [{id,name,args}] 포함(FE 가 무엇을 승인하는지 표시)."""
    evs = list(RunService(ApprovalAgent(), FakeRepo()).run("q", thread_id="t1"))
    req = next(e for e in evs if e.event == "approval.requested")
    tools = req.data["tools"]
    assert len(tools) == 1
    assert tools[0] == {"id": "c1", "name": "search_legal", "args": {"query": "거실"}}
