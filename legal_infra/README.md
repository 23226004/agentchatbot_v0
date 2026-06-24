# legal_infra

`legal_core` Protocol의 **구현체** — 클라이언트 의존(qdrant-client·rdflib·requests)을 여기 격리.
(3층: `legal_core`=인터페이스 / `legal_infra`=구현 / 앱=backend·db-admin·agent)

| 구현 | Protocol | 비고 |
|------|----------|------|
| `RemoteEmbedding` | `EmbeddingProvider` | 원격 8081 BGE-m3 `/v1/embeddings` (dense 1024, **sparse 후속**) |
| `QdrantVector` | `VectorRepository` | dense named vector(cosine)+payload 인덱스. sparse vector 슬롯은 예약(후속 하이브리드) |
| `FusekiGraph` | `GraphRepository` | `add_nt`(GSP PUT)·`expand`(default+named graph UNION, `.n3()` 안전바인딩)·`select` |

## 안전(Zero-Trust)
- `expand`/`select` 입력 IRI는 **절대 IRI + 허용 prefix** 검증 후 `.n3()` 직렬화 (SPARQL 주입 차단, 단위테스트 `tests/test_graph_safety.py`).
- raw SPARQL 문자열 API 미제공.

## 라이브 검증됨 (2026-06-24)
원격 임베딩(dense 1024) + 로컬 Qdrant(거실→제2조 0.752 1순위) + Fuseki(delegatesTo expand) 통합 동작 확인.

## 설치
```bash
uv pip install -e ./legal_core -e ./legal_infra
pytest legal_infra/tests   # 서버 불필요(안전 바인딩 테스트)
```
