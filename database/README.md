# database

VectorDB와 GraphDB를 **동시에** 관리하는 스토리지 정의 계층.

## 구조
| 폴더 | 책임 |
|------|------|
| `vector/` | VectorDB 설정·인덱스·임베딩 스키마 |
| `graph/` | GraphDB 노드·엣지·스키마 정의 |
| `migrations/` | 스키마 변경 이력 |

## 원칙
- 두 DB는 각각 독립 폴더로 관리하되, 상위에서는 단일 데이터 계층으로 본다.
- 실제 접근은 `backend/repositories/`의 인터페이스를 통한다.
