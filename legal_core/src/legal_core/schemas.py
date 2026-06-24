"""법령 GraphRAG 도메인 스키마 (순수 — 외부 의존 없음).

Design v0.3.1 §4. 모든 계층(backend·agent·db-admin)이 공유하는 값 객체.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class DenseSparse:
    """BGE-m3 하이브리드 임베딩 — dense + sparse(lexical weights) 동시.

    sparse 는 FlagEmbedding `lexical_weights` 포맷({token_id: weight})과 동일.
    Qdrant sparse 벡터({indices, values})로는 구현체가 변환한다.
    """

    dense: list[float]              # 길이 1024 (BGE-m3)
    sparse: dict[int, float]        # {token_id: weight}


@dataclass(frozen=True)
class Chunk:
    """임베딩 단위 = 법령 조(Article). 본문은 law.go.kr API에서 도출(자기정합)."""

    uri: str                        # canonical Article IRI (가지번호 포함)
    resource_id: str                # 법령ID (LegalResource, 버전 그룹 키)
    statute: str                    # 법령명 (예: "건축법")
    article_no: str                 # 표시용 조문번호 (예: "제2조", "제4조의2")
    article_key: str                # API 조문키 (statute 내 전역 유일)
    eff_date: str                   # 시행일자 "YYYY-MM-DD"
    ministry: str                   # 소관부처
    text: str                       # 임베딩 입력 (분할 시 윈도우)
    is_current: bool                # 현행 여부
    seq: int = 0                    # 분할 서브청크 인덱스(0=단일). uri는 조문 단위 유지
    article_text: str = ""          # 조문 전체 본문(답변·인용용; 모든 서브청크 공유). 빈값이면 text 사용


@dataclass(frozen=True)
class Hit:
    """벡터 검색 결과 1건."""

    uri: str
    score: float
    payload: dict


@dataclass(frozen=True)
class LawRef:
    """근거 조문 — FE citation 계약과 정렬(Design §6.1).

    conversation-store 가 이 값을 '방출 순간' 그대로 불변 스냅샷으로 동결한다.
    """

    id: str                         # str(UUIDv5(NS, uri)) — point.id 와 동일 규칙
    kind: str                       # "law" (1차) | "precedent"(후속)
    title: str                      # statute
    ref: str                        # 예: "건축법 제2조"
    snippet: str                    # 본문 발췌
    url: str                        # law.go.kr 출처 링크
    uri: str                        # canonical Article IRI (그래프 조인키)
    resource_id: str                # 법령ID
    eff_date: str                   # 시행일자
    score: float                    # 리랭크 점수
    article_text: str = ""          # 조문 전체 본문(답변 생성용; citation 표면엔 미노출)

    def to_citation(self) -> dict:
        """FE `citation.added` payload {id,kind,title,ref,snippet,url} 로 변환."""
        return {
            "id": self.id,
            "kind": self.kind,
            "title": self.title,
            "ref": self.ref,
            "snippet": self.snippet,
            "url": self.url,
        }


@dataclass(frozen=True)
class AnswerContext:
    """검색·관계확장 결과. RetrievalService 가 여기까지만 만들고 GPT 는 호출하지 않는다."""

    articles: list[LawRef]                      # 근거 조문 (리랭크 top-k)
    relations: list[tuple[str, str, str]] = field(default_factory=list)  # 확장된 (s,p,o)
    query: str = ""
