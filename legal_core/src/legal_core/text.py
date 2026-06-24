"""텍스트 분할 유틸 (순수). 긴 조문(별표·전문)을 임베딩 입력 한계 내로 슬라이딩 분할.

임베딩 서버(llama.cpp BGE-m3)는 단일 입력이 **ubatch(512토큰) 이내**여야 함(실측:
500자 OK, 1000자 500에러). 최악(1자=1토큰)에도 512 미만이도록 보수적 char cap.
Design §3.5: 이상치 슬라이딩 분할(겹침 64자).
"""

from __future__ import annotations

MAX_CHARS = 450      # ≤512토큰 보장(최악 1자=1토큰 가정). 실텍스트(~1.7자/토큰)는 여유
OVERLAP = 64


def split_windows(text: str, max_chars: int = MAX_CHARS, overlap: int = OVERLAP) -> list[str]:
    """text 를 max_chars 윈도우(겹침 overlap)로 분할. 짧으면 [text] 그대로."""
    text = text.strip()
    if len(text) <= max_chars:
        return [text] if text else []
    step = max(1, max_chars - overlap)
    windows = []
    for start in range(0, len(text), step):
        windows.append(text[start:start + max_chars])
        if start + max_chars >= len(text):
            break
    return windows
