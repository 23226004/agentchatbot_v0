# frontend

Svelte 기반 사용자 인터페이스. **FSD(Feature-Sliced Design)** 구조를 따른다.

## 구조 (`src/`)
| 레이어 | 책임 |
|--------|------|
| `app/` | 전역 설정, 라우팅, 프로바이더, 진입점 |
| `pages/` | 라우트 단위 페이지 조합 |
| `widgets/` | 독립적인 큰 UI 블록 (페이지 구성 요소) |
| `features/` | 사용자 행동 단위 기능 (검색, 질의 등) |
| `entities/` | 비즈니스 엔티티 (문서, 노드, 대화 등) |
| `shared/` | 공통 UI·유틸·API 클라이언트 |

## 의존 규칙 (상위 → 하위만 허용)
`app → pages → widgets → features → entities → shared`
