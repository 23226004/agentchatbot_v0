# legal_core

법령 GraphRAG **공유 코어** — `backend`·`agent`·`db-admin`이 의존하는 최하위 레이어.
(Design v0.3.1 §9.1: repositories/schemas를 독립 설치형 패키지로 분리해 `import src` 충돌·배포결합 제거)

## 구성
| 모듈 | 내용 |
|------|------|
| `schemas.py` | `DenseSparse`·`Chunk`·`Hit`·`LawRef`·`AnswerContext` (순수 값 객체) |
| `repositories.py` | `EmbeddingProvider`·`VectorRepository`·`Reranker`·`GraphRepository` Protocol + `EXPAND_PREDICATES_V1` |
| `ids.py` | canonical IRI(법령ID 기반, **가지번호 포함 R1**) + `point_id`=UUIDv5 |

## 원칙
- **순수**: 외부 의존 0. 구현체(Fuseki/Qdrant/FlagEmbedding/TEI)는 상위(backend/db-admin)가 Protocol을 만족하도록 제공.
- `point_id` ≡ FE citation id (단일 규칙), 조문 URI가 Vector↔Graph 조인키.

## 설치 (editable)
```bash
uv pip install -e ./legal_core      # 또는 pip install -e
```
backend·db-admin pyproject가 이 패키지를 의존성으로 선언한다. (agent 연결은 db-layer 슬라이스8/FR-12)

## 테스트
```bash
pytest legal_core/tests
```
