# 2026_06_20_Agent

VectorDB와 GraphDB를 함께 활용하는 AI Agent 시스템.

## 설계 원칙
- **SoC (관심사 분리)**: 각 폴더는 하나의 책임만 가진다.
- **FSD (Feature-Sliced Design)**: 프론트엔드 레이어 분리.
- **Zero-Trust**: 계층 간 경계에서 입력/권한을 항상 검증한다.
- **각 폴더는 README.md로 내부 구조를 문서화한다.**

## 최상위 폴더 (5)
| 폴더 | 책임 | 기술 |
|------|------|------|
| `frontend/` | 사용자 UI | Svelte (FSD) |
| `backend/` | API · 비즈니스 로직 | Python |
| `agent/` | LLM Agent · 추론 · 도구 | Python |
| `database/` | VectorDB + GraphDB 스토리지 정의 | - |
| `db-admin/` | DB 데이터 확인·관리, 추후 자동화 | Python |

## 의존 방향
```
frontend ──▶ backend ──▶ agent ──▶ database
                            ▲           │
                            └─ db-admin ┘ (데이터 점검·관리·자동화)
```
DB 세부 구현(Vector/Graph)은 `database/`에 격리되고, 상위 계층은
`backend/repositories/`의 추상 인터페이스를 통해서만 접근한다.
