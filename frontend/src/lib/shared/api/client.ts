// 백엔드 통신 경계(SoC). 위젯/페이지는 이 인터페이스만 알고 HTTP·SSE 는 모른다.
// mock 과 sse 가 동일 인터페이스를 구현해 교체 가능.
import type { AgentEvent } from './contracts';

export interface AgentClient {
  /** 사용자 메시지를 보내고 응답 이벤트 스트림을 받는다. model 명시 시 그 run 만 해당 모델로 실행. */
  send(text: string, model?: string | null): AsyncIterable<AgentEvent>;
  /** 승인 대기(approval.requested) 중인 run 을 승인/거절하고 이어지는 이벤트를 받는다(HITL).
   * approved 지정 시 그 도구 id 만 선택 실행(나머지 거절). 미지정=전체 승인(approve=true)/전체 거절(false). */
  approve(approve: boolean, approved?: string[]): AsyncIterable<AgentEvent>;
  /** 활성 스레드 지정(null=다음 send 에서 새로 생성). 이력 복원·새 대화 전환에 사용. */
  setThread(id: string | null): void;
  /** 현재 활성 스레드 id(없으면 null). */
  currentThread(): string | null;
  /** 진행 중인 run 을 중지(협조취소). 활성 run 없으면 no-op. 스트림은 terminal 받고 스스로 종결. */
  interrupt(): Promise<void>;
}
