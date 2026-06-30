// SvelteKit 앱 전역 타입. shallow routing(드로어 등 history 연동 UI)용 PageState 선언.
declare global {
  namespace App {
    // pushState/replaceState 로 history 에 싣는 페이지 상태(반응형 M2: 모바일 대화목록 드로어).
    interface PageState {
      navDrawer?: boolean; // 좌측 대화목록 드로어(M2)
      taskDrawer?: boolean; // 우측 계획·근거 패널 드로어(M3 — ≤1040)
    }
  }
}

export {};
