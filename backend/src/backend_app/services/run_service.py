
"""RunService — agent updates → FE SSE 8종 + transcript 영속 (권위=conversation-store §5).

**슬라이스3(영속화)**: 슬라이스8 인메모리 스켈레톤을 ConversationRepository 로 영속화.
agent `stream(stream_mode="updates")` 청크를 단일 지점에서 (a)SSE 변환 (b)transcript 영속
(c)seq DB 원자채번 (d)답변계층 최종본문(면책·버전·cite 위조검증). **커밋 후 방출**: 각 이벤트는
repo 쓰기(autocommit) 후 yield → 관찰순서=seq=DB순서.

| updates | SSE event(+seq) | 영속 |
|---------|-----------------|------|
| 실행 시작 | run.started | runs insert(running) = open_run |
| AIMessage.tool_calls | tool.call | messages(role=tool, tool_name/args) |
| ToolMessage | tool.result | 해당 도구셀 message.tool_result 갱신 |
| 도구 artifact(LawRef[]) | citation.added | citations freeze(전문) |
| 최종 AIMessage | message.completed | messages(role=agent, content_md=최종본문)+message_citations |
| interrupt | approval.requested | (다음 슬라이스4: runs.status=awaiting_approval) |
| 예외/종료 | error/run.done | runs.status·ended_at |

seq 는 thread 별 DB 원자채번(repo.next_seq). approval 재개·reconcile·fork 는 슬라이스4~5.
agent 는 `.stream(message, thread_id)` 만 만족하면 되도록 덕타이핑.
"""

from __future__ import annotations

import logging
import re
import threading
from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Any

# IGNORECASE: 대문자 `[[CITE:id]]` 도 매칭 — 안 하면 위조검증 우회 + 무인용 raw 저장(렌더 불가) 갭(교차검증).
# 정규화(_CITE.sub)가 캡처 id 를 소문자 `[[cite:...]]` 마커로 통일해 저장·FE 렌더 정합.
_log = logging.getLogger("conversation.run")    # run 시작 시 사용 모델 등 운영 로그

_CITE = re.compile(r"\[\[cite:([^\]]+)\]\]", re.IGNORECASE)

DISCLAIMER = "※ 본 답변은 법률자문이 아니며, 정확한 판단은 전문가 확인이 필요합니다."

# 서버가 권위로 부착하는 줄(버전 고지·면책)을 LLM 초안이 사칭하지 못하게 제거하기 위한 패턴.
# (적대검증: LLM이 가짜 "기준 시행일자"·면책을 본문에 직접 써넣어 공식인 척할 수 있었음)
# **유니코드·인라인 회피 차단(2차 적대검증)**: 줄앵커(^)를 빼 인라인 배치도 잡고, 토큰 사이 공백을
# 일반/탭/**전각공백(U+3000)**까지 허용([ \t　]*). zero-width 문자는 _compose 에서 사전 제거.
# (3차서 '공식 법률자문' 류 삽입어 우회·인용 의미검증 갭이 나왔으나 **LLM 제어 단계로 보류** — 시스템 틀 우선)
_AUTH_WS = "[ \t　]*"   # 일반·탭·전각공백(U+3000)
_AUTHORITY_LINE = re.compile(
    rf"(?:⚖️{_AUTH_WS}기준{_AUTH_WS}시행일자{_AUTH_WS}[:：]"
    rf"|※{_AUTH_WS}본{_AUTH_WS}답변은{_AUTH_WS}법률자문).*$",
    re.MULTILINE)
# 권위 줄 사칭에 쓰이는 invisible/zero-width 문자(ZWSP·ZWNJ·ZWJ·word-joiner·BOM) — 본문서
# 제거 후 사칭 검사(`⚖️​기준`처럼 토큰 사이 zero-width 삽입으로 strip 회피하는 벡터 차단).
_ZERO_WIDTH = dict.fromkeys(
    (0x200B, 0x200C, 0x200D, 0x2060, 0xFEFF), None)

# per-tool Stage 2: 선택적 승인에서 거절된 도구셀의 결과(transcript 정본). checkpoint 에는 거절분
# tool_call 이 제거돼 LLM 이 보지 않음(dual-body) — 이 마커는 UI/감사용 transcript 표시.
_REJECTED_TOOL = "사용자가 이 도구 실행을 거부했습니다."
# 비정상 종결(취소/크래시)로 결과를 못 받은 도구셀 마커 — '영구 pending' 고아 방지(XV D-2).
_INTERRUPTED_TOOL = "실행이 중단되어 결과를 받지 못했습니다."

# tool.result 표시/저장 상한(G3): 법령검색 도구가 article_text 전문 × top-k 를 SSE 로 흘리는 정보과다·
# 저장팽창 차단. **LLM 컨텍스트(checkpoint ToolMessage)·citation 전문(freeze)은 원본 유지** — 여기서
# 자르는 건 도구셀 '표시값'(transcript·SSE)뿐이라 답변 정확성·인용 복원에 무영향.
_MAX_TOOL_RESULT = 8000


def _truncate_tool_result(content: Any) -> Any:
    """문자열 도구결과를 표시 상한으로 자른다(인디케이터 부착). 비-문자열은 그대로."""
    if isinstance(content, str) and len(content) > _MAX_TOOL_RESULT:
        return content[:_MAX_TOOL_RESULT] + f"\n…(표시 생략: 전체 {len(content)}자 — 전문은 인용 참조)"
    return content


def _text_of(content: Any) -> str:
    """AIMessage.content 정규화 → 평문. content 는 str 또는 콘텐츠블록 list(둘 다 LangChain 표준).

    list 형태(예: [{"type":"text","text":...}], Anthropic식)를 str 로 보지 않고 누락하면
    최종 본문·cite 위조검증이 통째로 깨진다(실제 LangGraph 통합검증에서 발견).
    """
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if isinstance(block, str):
                parts.append(block)
            elif isinstance(block, dict) and block.get("type") == "text":
                t = block.get("text")
                if isinstance(t, str):
                    parts.append(t)
        return "".join(parts)
    return ""


@dataclass(frozen=True)
class RunEvent:
    """FE 로 나가는 SSE 이벤트 한 건. seq=thread 별 DB 원자채번(관찰순서=DB순서)."""

    event: str
    data: dict[str, Any]
    seq: int


def sse_wire(ev: RunEvent) -> str:
    """RunEvent → SSE 와이어 포맷 문자열(`event:`/`data:`). API 계층이 사용."""
    import json

    # 이벤트명에서 개행/CR 제거(SSE 프레이밍 인젝션 방어). data 는 json.dumps 가 제어문자 이스케이프.
    name = ev.event.replace("\n", "").replace("\r", "")
    # id: seq → 브라우저 EventSource 가 Last-Event-ID 로 자동 추적(끊기면 그 seq 부터 재연결, G4).
    return (f"id: {ev.seq}\nevent: {name}\n"
            f"data: {json.dumps({**ev.data, 'seq': ev.seq}, ensure_ascii=False)}\n\n")


@dataclass
class _Run:
    """런 누적 상태(영속 보조). seq·메시지·citation 자체는 DB가 정본.

    승인 재개(run→resume)는 다른 호출이라 tool_call↔message 상관은 in-memory 가 아니라
    DB키(run_id, tool_call_id)로 한다 → 여기엔 tool_msg 맵을 두지 않는다.
    """

    run_id: str
    frozen: set[str] = field(default_factory=set)            # 이 런에서 freeze 한 citation id
    cited_eff: dict[str, str] = field(default_factory=dict)  # id → eff_date(버전 머리고지용)
    draft: str = ""                                          # 최종 agent 초안 본문
    parent_id: str | None = None                            # 턴 루트(user 메시지 id) — 셀 그룹핑(§3.2)
    model: str | None = None                                # 이 런에 선택된 LLM(답변 메시지에 기록)


def _is_tool_message(msg: Any) -> bool:
    return msg.__class__.__name__ == "ToolMessage"


def _tool_calls(msg: Any) -> list[dict]:
    calls = getattr(msg, "tool_calls", None)
    return calls or []


def _articles_of(artifact: Any) -> list[Any]:
    """ToolMessage.artifact 가 (live) AnswerContext 면 articles(LawRef[]) 반환, 아니면 [].

    **불변식**: artifact 추출은 **stream 방출 즉시**(live AnswerContext)에만 해야 한다. checkpoint
    재수화를 거치면 artifact 가 dict 로 타입손실(교차검증 확인)되어 LawRef 속성 접근이 깨진다.
    dict(=checkpoint 유래)가 여기 오면 조용한 누락 대신 **명확히 실패**(향후 checkpoint 재조회
    버그를 즉시 노출). 정상 경로(RunService 가 stream 에서 추출)는 항상 live 객체라 트리거 안 됨.
    """
    if artifact is None:
        return []
    arts = getattr(artifact, "articles", None)
    if arts is None and isinstance(artifact, dict):
        raise TypeError(
            "artifact 가 dict(checkpoint 재수화 타입손실) — citation 추출은 방출 즉시 live "
            "AnswerContext 에서만 (불변식 위반)")
    return list(arts) if arts else []


def _interrupt_detail(interrupt: Any) -> Any:
    """LangGraph `__interrupt__` 페이로드에서 FE 표시용 detail 추출(값 없으면 사유 문자열).

    interrupt_before(정적)는 값이 비어있을 수 있고, 동적 `interrupt(value)`는 value 를 담는다.
    """
    try:
        vals = [getattr(i, "value", None) for i in interrupt]
        vals = [v for v in vals if v is not None]
        if vals:
            return vals if len(vals) > 1 else vals[0]
    except TypeError:
        pass
    return "approval_required"


def _freeze_payload(ref: Any) -> dict:
    """LawRef → citations freeze dict(전문·포인터 포함). SSE 표면(to_citation)보다 넓다."""
    return {
        "id": ref.id, "kind": ref.kind, "title": ref.title, "ref": ref.ref,
        "snippet": ref.snippet, "url": ref.url, "article_text": ref.article_text,
        "law_uri": ref.uri, "resource_id": ref.resource_id, "eff_date": ref.eff_date,
    }


class RunService:
    def __init__(self, agent: Any, repo: Any, default_model: str | None = None) -> None:
        # agent = 단일 agent **또는** {model_id: agent} 레지스트리(런타임 모델 선택, GPT/로컬).
        # 단일이면 settings.llm_model 을 키로(없으면 "default") 1엔트리 레지스트리로 정규화.
        if isinstance(agent, dict):
            self.agents: dict[str, Any] = dict(agent)
        else:
            key = getattr(getattr(agent, "settings", None), "llm_model", None) or "default"
            self.agents = {key: agent}
        self.default_model = default_model or next(iter(self.agents))
        self.agent = self.agents[self.default_model]   # summarize/fork·단일 호환(기본 모델)
        self.repo = repo    # ConversationRepository (영속·seq 채번)
        # run_id별 협조적 취소 플래그(사용자 interrupt). pump 스레드의 _drive 루프가 청크 경계서
        # is_set()을 확인 → 동기 LLM 호출은 못 끊으나 다음 청크에서 안전히 중지(강제중단 회피).
        self._cancels: dict[str, threading.Event] = {}

    def request_cancel(self, run_id: str) -> bool:
        """running run 에 협조적 취소 신호. 로컬 pump 가 도는 run 이면 True(루프가 다음 청크서 종결)."""
        ev = self._cancels.get(run_id)
        if ev is None:
            return False
        ev.set()
        return True

    def interrupt_paused(self, thread_id: str, run_id: str) -> bool:
        """승인 대기(일시정지)로 멈춘 run 을 중지: CAS awaiting_approval→interrupted + 그래프 정리.

        일시정지 run 은 도는 루프가 없어 취소 플래그로 못 끊으므로 DB 에서 직접 종결한다. 성공=True
        (동시 resume/timeout 패자는 False). 정리는 reject 와 동일(미결 도구호출 stale 방지)."""
        if not self.repo.try_transition(run_id, "awaiting_approval", "interrupted", ended=True):
            return False
        self.repo.set_pending_approval(run_id, "rejected")
        self.repo.resolve_orphan_tool_cells(run_id, _INTERRUPTED_TOOL)  # 미결 도구셀 tool_result NULL→마커(교차검증 A2)
        self._clear_rejected_graph(thread_id)
        return True

    def run(self, message: str, thread_id: str, model: str | None = None) -> Iterator[RunEvent]:
        # 모델 선택(런타임): 지정 없으면 기본. 알 수 없는 모델은 기본으로 폴백(엔드포인트가 422 선검증).
        model = model if model in self.agents else self.default_model
        agent = self.agents[model]
        # open_run: 활성 run 존재 시 ActiveRunExists 전파(API 409). 첫 yield 전 수행. model 기록.
        run_id = self.repo.open_run(thread_id, model=model)
        self._cancels[run_id] = threading.Event()       # 사용자 interrupt 협조 취소 플래그
        rs = _Run(run_id=run_id, model=model)
        _log.info("run start run_id=%s thread=%s model=%s", run_id, thread_id, model)
        yield self._emit("run.started",
                         {"run_id": run_id, "thread_id": thread_id, "model": model}, thread_id)
        # 사용자 메시지 영속(role=user) — transcript 정본의 일부(이력복원·fork 시드에 질문 필요).
        # SSE 이벤트는 없음(사용자는 자기 메시지를 이미 앎). seq 는 소비.
        # **보호**: 이 쓰기는 _drive try 밖이라, 실패(예: NUL DataError) 시 run 이 running 고아로
        # 남아 thread 가 잠긴다 → CAS error 종결+error 이벤트로 정리(교차검증 회귀 수정).
        try:
            mid, _ = self.repo.add_message(thread_id, role="user", run_id=run_id, content_md=message)
            rs.parent_id = mid                          # 턴 루트 — 이후 도구셀·답변을 이 아래로 그룹핑
        except Exception:  # noqa: BLE001
            _log.exception("run error (user msg persist) run_id=%s thread=%s", run_id, thread_id)
            self.repo.try_transition(run_id, "running", "error", ended=True)
            yield self._emit("error", {"run_id": run_id, "message": "실행 중 오류가 발생했습니다."}, thread_id)
            return
        yield from self._drive(agent.stream(message, thread_id=thread_id), rs, thread_id)

    def resume(self, thread_id: str, approve: bool = True,
               approved_ids: list[str] | None = None) -> Iterator[RunEvent]:
        """승인 대기 중인 run 을 승인/거절(슬라이스4) 또는 **선택적 실행**(per-tool Stage 2).

        - approved_ids 미지정: 기존 전체 단위(approve=True 전량 승인 / False 전량 거절).
        - approved_ids 지정(Stage 2): 그 중 유효 도구만 실행, 나머지는 거절. 빈 교집합=전량 거절과 동일,
          전량=전량 승인. 부분일 때만 그래프 surgery(approve_partial)로 미결 AIMessage 를 승인분만 남김.

        awaiting_approval→running/rejected 는 **CAS**(try_transition)로 단 1명만 진행(동시 resume 보호).
        """
        active = self.repo.get_active_run(thread_id)
        if active is None or active[1] != "awaiting_approval":
            raise ValueError("재개할 승인-대기 run 이 없습니다")
        run_id = active[0]

        # 승인 범위 산정. None=전체 단위(approve bool), set=선택적(유효 도구 id 교집합).
        if approved_ids is None:
            approved_set = None if approve else set()
        else:
            pending = {t["id"] for t in self.repo.get_pending_tool_calls(run_id)}
            approved_set = set(approved_ids) & pending

        # 전량 거절(또는 선택에서 아무것도 승인 안 함) → rejected 종결.
        if approved_set is not None and not approved_set:
            if not self.repo.try_transition(run_id, "awaiting_approval", "rejected", ended=True):
                raise ValueError("이미 처리된 승인입니다")  # 동시 resume 패자
            self.repo.set_pending_approval(run_id, "rejected")
            self.repo.resolve_orphan_tool_cells(run_id, _REJECTED_TOOL)  # 미결 도구셀 tool_result NULL→마커(교차검증 A2)
            self._clear_rejected_graph(thread_id)   # 체크포인트 정합화(stale interrupt 방지)
            yield self._emit("run.done", {"run_id": run_id, "status": "rejected"}, thread_id)
            return

        # 승인(전량 또는 부분) → running 전이 후 재개.
        if not self.repo.try_transition(run_id, "awaiting_approval", "running"):
            raise ValueError("이미 처리된 승인입니다")
        self._cancels.setdefault(run_id, threading.Event())   # 재개 후에도 interrupt 가능
        # 재개는 **원래 run 의 모델**로 이어간다(같은 대화·같은 LLM). 없으면 기본.
        model = self.repo.get_run_model(run_id) or self.default_model
        agent = self.agents.get(model, self.agent)
        rs = _Run(run_id=run_id, model=model,
                  parent_id=self.repo.get_turn_root(run_id))  # 같은 턴 루트 아래로

        # 부분 승인: 미결 AIMessage 에서 거절분 tool_call 제거(surgery) + 셀 분류를 **단일 tx**로
        # (mark_partial_approval: 거절→rejected+마커, 나머지 NULL→approved; XV S5 비원자 반-마킹 방지).
        # 전량 승인(approved_set=None)이면 무수술 — 기존 set_pending_approval 경로.
        if approved_set is not None:
            ap = getattr(agent, "approve_partial", None)
            rejected = ap(thread_id, approved_set) if callable(ap) else []
            self.repo.mark_partial_approval(run_id, rejected, _REJECTED_TOOL)
            for tcid in rejected:
                yield RunEvent("tool.result",
                               {"id": tcid, "content": _REJECTED_TOOL, "rejected": True},
                               self.repo.next_seq(thread_id))
        else:
            self.repo.set_pending_approval(run_id, "approved")

        yield from self._drive(agent.resume(thread_id), rs, thread_id)

    def _clear_rejected_graph(self, thread_id: str) -> None:
        """거절 시 그래프의 미결 도구호출을 정리(없으면 다음 run 이 INVALID_CHAT_HISTORY 로 잠김)."""
        rj = getattr(self.agent, "reject_pending", None)
        if callable(rj):
            rj(thread_id)

    def summarize(self, thread_id: str, from_seq: int | None = None,
                  to_seq: int | None = None) -> dict | None:
        """thread 의 [from_seq, to_seq] 범위 대화를 LLM 요약→summaries 영속(긴 컨텍스트 압축).

        범위 미지정 시 전체. user/agent content_md 만 텍스트로(도구셀 제외). 요약할 내용 없으면 None.
        """
        msgs = [m for m in self.repo.get_thread_messages(thread_id)
                if m.get("role") in ("user", "agent") and (m.get("content_md") or "").strip()
                and (from_seq is None or m["seq"] >= from_seq)
                and (to_seq is None or m["seq"] <= to_seq)]
        if not msgs:
            return None
        text = "\n".join(f'{m["role"]}: {m["content_md"]}' for m in msgs)
        summary = self.agent.summarize(text)
        cf, ct = msgs[0]["seq"], msgs[-1]["seq"]
        sid = self.repo.add_summary(thread_id, cf, ct, summary)
        return {"id": sid, "content_md": summary, "covers_from_seq": cf, "covers_to_seq": ct}

    def fork(self, parent_thread_id: str, fork_point_message_id: str) -> str:
        """fork(§3.4 참조모델 + §6 F-1): 새 thread 생성(메시지 복사X) + checkpoint state 시드.

        repo.fork_thread 가 참조분기(조상 prefix 가시성·seq 연속성)를, agent.fork_state 가 새 thread
        checkpoint 를 transcript 정본(조상 prefix ≤ fork_point)으로 시드해 분기 후 대화가 이어진다.
        """
        new_id = self.repo.fork_thread(parent_thread_id, fork_point_message_id)
        seed = getattr(self.agent, "fork_state", None)
        if callable(seed):
            seed(new_id, self.repo.get_thread_messages(new_id))   # 새 thread = 부모 prefix ≤ fork_point
        return new_id

    def _drive(self, stream: Iterator, rs: _Run, thread_id: str) -> Iterator[RunEvent]:
        """스트림을 소비해 이벤트를 영속·방출. interrupt 시 awaiting_approval 로 일시정지(종결 안 함)."""
        run_id = rs.run_id
        cancel = self._cancels.get(run_id)
        try:
            try:
                for chunk in stream:
                    # 사용자 interrupt(협조 취소): 청크 경계서 플래그 확인 → CAS interrupted 종결 후 종료.
                    # 동기 LLM 호출 자체는 못 끊으므로 "다음 청크"까지 지연될 수 있다(강제중단 회피).
                    if cancel is not None and cancel.is_set():
                        # **checkpoint 정리(XV D-1)**: 재개 중 취소면 진행중 도구턴(미커밋 도구결과)이
                        # checkpoint 에 남아 다음 턴 LLM 이 인용 → message_citations 링크 없는 citation 이라
                        # cite_forgery 로 thread 영구 사망. 먼저 stream.close()로 in-flight 를 정착시킨 뒤
                        # (mid-stream update_state 는 'ID 없음'으로 실패) clear_interrupted_tools 로 그
                        # 도구턴을 제거 → 다음 턴 깨끗이 새 시작. + 미완 도구셀 마커 정합(XV D-2 고아 방지).
                        try:
                            stream.close()
                            clr = getattr(self.agent, "clear_interrupted_tools", None)
                            if callable(clr):
                                clr(thread_id)
                        except Exception:  # noqa: BLE001 — 정리 실패해도 취소는 interrupted 로 종결
                            _log.warning("cancel cleanup failed run_id=%s thread=%s (계속 interrupted 종결)",
                                         run_id, thread_id)        # G6: 정리 실패 가시화(잔여는 다음 턴/sweep)
                        self.repo.resolve_orphan_tool_cells(run_id, _INTERRUPTED_TOOL)
                        if self.repo.try_transition(run_id, "running", "interrupted", ended=True):
                            yield self._emit("run.done",
                                             {"run_id": run_id, "status": "interrupted"}, thread_id)
                        return
                    if isinstance(chunk, dict) and "__interrupt__" in chunk:
                        self.repo.set_run_status(run_id, "awaiting_approval")
                        self.repo.set_pending_approval(run_id, "pending")
                        # FE 계약 §4.2: 승인모달 {id, action, detail} + **tools**(대기 도구 목록 [{id,name,args}])
                        # → FE 가 "search_legal('건폐율') 실행?" 처럼 무엇을 승인하는지 표시(per-tool 정보노출).
                        # 도구셀은 이 시점 이미 영속(AIMessage 청크가 interrupt 청크보다 먼저 처리됨).
                        # 승인/거절은 전체 단위 유지(개별 선택 실행은 후속). run_id 는 하위호환.
                        yield self._emit("approval.requested",
                                         {"id": run_id, "run_id": run_id, "action": "tool",
                                          "tools": self.repo.get_pending_tool_calls(run_id),
                                          "detail": _interrupt_detail(chunk["__interrupt__"])},
                                         thread_id)
                        return  # 일시정지: finalize/run.done 없이 멈춤(resume 이 이어감)
                    yield from self._handle_chunk(chunk, rs, thread_id)
            except Exception:  # noqa: BLE001 — 상세 예외는 사용자에 노출 안 함(내부정보 누출 방어)
                # **G6**: 사용자엔 일반 메시지지만 **운영자에겐 진짜 예외를 로깅**(안 하면 run 이 왜 죽었는지
                # — LLM 타임아웃/검색 다운/PG/버그 — 진단 불가했음). exc_info 자동 포함.
                _log.exception("run error run_id=%s thread=%s", run_id, thread_id)
                # CAS: sweep/reconcile/타 인스턴스 interrupt 가 이미 running 밖으로 옮긴 run 은 되살리지
                # 않고(부활 방지), **error 도 우리가 전이 성공했을 때만** 방출 — 이미 terminal(interrupted)
                # 이면 run.done 뒤 error 가 덧붙는 double-terminal 이 됐다(교차검증 Medium, 교차 인스턴스).
                if self.repo.try_transition(run_id, "running", "error", ended=True):
                    self.repo.resolve_orphan_tool_cells(run_id, _INTERRUPTED_TOOL)  # 고아 셀 정합(XV D-2)
                    yield self._emit("error",
                                     {"run_id": run_id, "message": "실행 중 오류가 발생했습니다."}, thread_id)
                return

            # _finalize 가 종결 전이(commit_agent_answer CAS=완료 / try_transition=forgery error)와
            # message.completed·run.done·error 방출을 **모두 CAS 게이트로** 수행한다. 외부 종결(교차
            # 인스턴스 interrupt·sweep)이면 영속·방출 없이 조용히 끝남(부활·orphan·이중종결 0).
            yield from self._finalize(rs, thread_id)
        except GeneratorExit:
            # **best-effort** 정리: 제너레이터가 명시적으로 close 될 때(GeneratorExit=BaseException,
            # 위 except Exception 우회) 아직 running 이면 error 로 종결. awaiting_approval(정상
            # 일시정지)은 CAS 불일치로 보존(durable). **한계**: 블로킹 agent 스트림 도중 클라 끊김은
            # uvicorn 이 body/gen 을 즉시 close 못 해 이 핸들러가 안 탈 수 있음 → 고아 running 은
            # `timeout_stale_runs`(TTL sweep)·부팅 reconcile 로 회수. 근본해법=run↔stream 디커플(후속).
            if self.repo.try_transition(run_id, "running", "error", ended=True):
                self.repo.resolve_orphan_tool_cells(run_id, _INTERRUPTED_TOOL)  # 고아 셀 정합(XV D-2)
                _log.warning("run closed mid-stream (GeneratorExit→error) run_id=%s thread=%s",
                             run_id, thread_id)         # G6: 비정상 close(클라 끊김 등) 가시화
            raise
        finally:
            # 취소 플래그 정리(누수 방지). 일시정지(approval) 시도 _drive 가 끝나며 제거되고,
            # resume 이 setdefault 로 재생성하므로 무해. 일시정지 run 의 interrupt 는 interrupt_paused(DB직접).
            self._cancels.pop(run_id, None)

    # ── helpers ──────────────────────────────────────────────────────────────
    def _emit(self, event: str, data: dict, thread_id: str) -> RunEvent:
        """seq 원자채번 후 RunEvent. (메시지 없는 이벤트용 — 영속은 호출부에서 먼저 수행)"""
        return RunEvent(event, data, self.repo.next_seq(thread_id))

    def _handle_chunk(self, chunk: dict, rs: _Run, thread_id: str) -> Iterator[RunEvent]:
        for _node, payload in chunk.items():
            if not isinstance(payload, dict):
                continue
            for msg in payload.get("messages", []):
                yield from self._handle_message(msg, rs, thread_id)

    def _handle_message(self, msg: Any, rs: _Run, thread_id: str) -> Iterator[RunEvent]:
        # 1) 도구 호출 → 도구셀 message(role=tool) 생성. tool_call_id 영속(승인 재개 시 결과 상관).
        #    seq 를 add_message 트랜잭션 내부에서 채번(seq=None) → seq+message 원자(갭 제거).
        for call in _tool_calls(msg):
            _mid, seq = self.repo.add_message(
                thread_id, role="tool", run_id=rs.run_id, parent_id=rs.parent_id,
                tool_name=call.get("name"), tool_args=call.get("args"),
                tool_call_id=call.get("id"))
            yield RunEvent("tool.call",
                           {"id": call.get("id"), "name": call.get("name"), "args": call.get("args")}, seq)
        # 2) 도구 결과(ToolMessage) → (run_id, tool_call_id)로 도구셀 갱신 + artifact freeze
        if _is_tool_message(msg):
            tcid = getattr(msg, "tool_call_id", None)
            content = _truncate_tool_result(getattr(msg, "content", ""))   # 표시값만 상한(G3)
            seq = self.repo.next_seq(thread_id)
            if tcid:
                self.repo.set_tool_result(rs.run_id, tcid, content)
            yield RunEvent("tool.result", {"id": tcid, "content": content}, seq)
            for ref in _articles_of(getattr(msg, "artifact", None)):
                if ref.id in rs.frozen:
                    continue  # 동일 조문 중복 인용은 한 번만
                self.repo.freeze_citation(_freeze_payload(ref))  # 전역 동결(전문)
                rs.frozen.add(ref.id)
                rs.cited_eff[ref.id] = ref.eff_date
                yield self._emit("citation.added", {**ref.to_citation()}, thread_id)
            return
        # 3) 최종 답변 초안(tool_calls 없는 AIMessage 본문). content 정규화(str/블록 list).
        if not _tool_calls(msg):
            content = _text_of(getattr(msg, "content", ""))
            if content.strip():
                rs.draft = content

    _NO_ANSWER = "요청하신 내용에 대한 근거를 찾지 못해 답변을 생성하지 못했습니다. 질문을 구체화해 다시 시도해 주세요."

    def _finalize(self, rs: _Run, thread_id: str) -> Iterator[RunEvent]:
        """최종 본문 산출·영속(message.completed/run.done 또는 error 방출). 종결 전이는 **CAS 원자**.

        **외부 종결(교차 인스턴스 interrupt·sweep) 방어가 영속과 원자**(교차검증 Medium 재수정): 진입시점
        get_run 가드는 get_run~INSERT 사이 TOCTOU 창이 남았다. 답변은 `commit_agent_answer`(CAS+INSERT
        한 tx)로, error/forgery 는 try_transition 성공 시에만 방출 → interrupted run 에 orphan 답변·
        stranded error 가 0(영속·방출이 전부 CAS 게이트).
        """
        body = rs.draft
        # 빈 초안(실 LLM이 그라운딩 실패 시 빈 content 반환 — 교차검증) → 면책만 남는 빈 답변 대신
        # 명시적 "근거 없음" 안내를 본문으로(빈 답변 UX 갭 방지).
        if not body.strip():
            body = self._NO_ANSWER
        # 마커 공백 정규화(교차검증): LLM 이 `[[cite: id ]]` 처럼 콜론 뒤/앞에 공백을 넣으면 캡처 id 가
        # 공백째라 frozen id 와 불일치 → **정당 인용이 cite_forgery 로 오탐돼 답변 전체가 막힌다**.
        # 본문 자체를 무공백 마커로 치환해 추출·저장·FE 렌더가 모두 동일 id 를 쓰게 한다(fail-closed 유지).
        body = _CITE.sub(lambda m: f"[[cite:{m.group(1).strip()}]]", body)
        used = _CITE.findall(body)
        # cite 위조검증(§5.1 C-b): 본문 [[cite:id]] ⊆ **thread 전역** 동결 citation id.
        # **per-run 이 아니라 thread 스코프**(교차검증 HIGH): 멀티턴에서 LLM 이 이전 턴 citation 을
        # 도구 재호출 없이 정당하게 재인용할 수 있다(checkpoint 이력 근거) — 이전 턴 freeze 도 valid.
        valid = rs.frozen | {str(c["id"]) for c in self.repo.get_thread_citations(thread_id)}
        forged = [cid for cid in used if cid not in valid]
        if forged:
            _log.warning("run cite_forgery run_id=%s thread=%s forged=%s",  # G6: 위조 차단 가시화
                         rs.run_id, thread_id, forged)
            # CAS 게이트: 우리가 running→error 를 이겼을 때만 방출(외부종결 run 에 stranded error 방지).
            if self.repo.try_transition(rs.run_id, "running", "error", ended=True):
                # FE 계약 §4.2 error 는 {message} 를 읽는다 → message 키 항상 포함(reason/forged 는 부가).
                yield self._emit("error",
                                 {"run_id": rs.run_id, "message": "근거 없는 인용이 감지되어 답변을 보류했습니다.",
                                  "reason": "cite_forgery",
                                  "forged": forged, "valid": list(valid)}, thread_id)
            return

        cited = list(dict.fromkeys(used))             # 본문에 실제 인용된 id(순서·중복제거)
        final = self._compose(body, cited, rs)
        # **원자**: 답변 영속 + running→completed CAS 를 한 tx 로. 외부 종결이면 None=미영속(orphan 0).
        res = self.repo.commit_agent_answer(thread_id, rs.run_id, content_md=final,
                                            citation_ids=cited, parent_id=rs.parent_id, model=rs.model)
        if res is None:
            return                                    # 외부 종결됨 — message.completed/run.done 미방출
        _mid, seq = res
        yield RunEvent("message.completed",
                       {"text": final, "content_type": "markdown", "citations": cited}, seq)
        yield self._emit("run.done", {"run_id": rs.run_id}, thread_id)
        _log.info("run completed run_id=%s thread=%s cites=%d len=%d",  # G6: 정상 완료
                  rs.run_id, thread_id, len(cited), len(final))

    @staticmethod
    def _compose(body: str, cited: list[str], rs: _Run) -> str:
        """최종 본문 = 버전고지(머리) + 초안 + 면책. 초안의 서버권위 줄 사칭은 strip."""
        # zero-width 제거 후 사칭 줄 strip(유니코드·인라인 회피 차단, 2차 적대검증).
        body = _AUTHORITY_LINE.sub("", body.translate(_ZERO_WIDTH)).strip()
        if not body:
            # 초안이 권위/면책 줄만이었던 경우 — strip 후 본문이 비면 면책만 남는 빈답변이 된다.
            # 명시적 "근거 없음" 안내로 대체(적대검증 UX 갭).
            body = RunService._NO_ANSWER
        parts: list[str] = []
        eff_dates = sorted({rs.cited_eff[c] for c in cited if c in rs.cited_eff})
        if eff_dates:
            parts.append(f"⚖️ 기준 시행일자: {', '.join(eff_dates)}")
        parts.append(body)
        parts.append(DISCLAIMER)
        return "\n\n".join(p for p in parts if p)
