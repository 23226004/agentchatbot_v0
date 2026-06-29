// 단일 스위치: 빈 문자열이면 mock 모드(백엔드 없이 동작), URL 이면 실 backend 연동.
// 이 값 하나가 SSE 클라이언트(mock↔sse)와 에이전트 프로파일 로드(시드↔GET /agents)를 함께 바꾼다.
//
// 소스 수정 없이 .env 의 VITE_API_BASE 로 주입한다. 예) VITE_API_BASE=http://localhost:8000
// backend CORS 기본 허용 origin 은 http://localhost:5180 (우리 dev 포트).
export const API_BASE: string = import.meta.env.VITE_API_BASE ?? '';
