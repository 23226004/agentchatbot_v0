// 에이전트 프로파일(표시용) — agent-platform.md "에이전트 = 프로파일(데이터)" 정합.
// FE는 표시 필드만 보유. 프롬프트/도구/검색 바인딩은 backend 프로파일(별도).
export interface AgentProfile {
  id: string;
  label: string; // 헤더·툴팁 표시명
  abbr: string; // 레일 아바타 글자
  accent?: string; // 활성 시 --accent override(도메인 색). 없으면 기본 토큰.
  ready: boolean; // 구현 여부(false=예정·비활성)
}
