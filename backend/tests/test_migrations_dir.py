"""_migrations_dir() 해소 회귀 — 설치 레이아웃 무관 견고화(컨테이너 부팅테스트 적발 결함 못박음).

과거: repo-상대 경로(parents[3]/database/migrations)만 → 비편집 설치(휠/컨테이너) 시
`…/lib/database/migrations` 로 오해소·FileNotFound 부팅실패. 수정: MIGRATIONS_DIR override + repo 폴백 + 명확 실패.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from backend_app.db import _migrations_dir


def test_env_override_used(monkeypatch, tmp_path):
    monkeypatch.setenv("MIGRATIONS_DIR", str(tmp_path))
    assert _migrations_dir() == tmp_path


def test_env_override_missing_dir_raises(monkeypatch, tmp_path):
    monkeypatch.setenv("MIGRATIONS_DIR", str(tmp_path / "nope"))
    with pytest.raises(RuntimeError, match="MIGRATIONS_DIR"):
        _migrations_dir()


def test_repo_fallback_when_no_env(monkeypatch):
    monkeypatch.delenv("MIGRATIONS_DIR", raising=False)
    d = _migrations_dir()                    # dev/editable: repo_root/database/migrations 존재
    assert d.is_dir()
    assert (d / "0001_conversation_store.sql").is_file()


def test_real_sql_files_present(monkeypatch):
    """동봉/해소된 디렉터리에 부팅이 적용하는 SQL 이 실제 존재(빈 디렉터리 silent no-op 방지)."""
    monkeypatch.delenv("MIGRATIONS_DIR", raising=False)
    d = _migrations_dir()
    sqls = sorted(p.name for p in Path(d).glob("*.sql"))
    assert sqls and sqls[0].startswith("0001")
