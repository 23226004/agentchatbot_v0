# backend

Python 기반 API 및 비즈니스 로직 계층. **SoC**와 **Zero-Trust**를 준수한다.

## 구조 (`src/`)
| 폴더 | 책임 |
|------|------|
| `api/` | 라우터·엔드포인트 (입력 검증, 인증) |
| `core/` | 설정, 보안, 공통 미들웨어 |
| `services/` | 도메인 비즈니스 로직 |
| `repositories/` | DB 접근 추상화 인터페이스 (Vector/Graph 구현 은닉) |
| `schemas/` | 요청·응답 DTO / 검증 스키마 |

## 원칙
- `api`는 `services`만, `services`는 `repositories`만 호출한다.
- DB 구현 교체가 `repositories` 경계 안에서만 일어나도록 한다.
