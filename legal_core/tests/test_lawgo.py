"""parse_law_service / flatten_article 단위테스트 — 적재 진입점 회귀 방지."""

from __future__ import annotations

from legal_core.lawgo import flatten_article, parse_hierarchy, parse_law_service

_FIXTURE = {
    "법령": {
        "기본정보": {
            "법령ID": "001823", "법령명_한글": "건축법", "시행일자": "20260227",
            "소관부처": {"content": "국토교통부"}, "공포일자": "20250826",
        },
        "조문": {"조문단위": [
            {"조문번호": "1", "조문여부": "전문", "조문내용": "        제1장 총칙"},  # 제외돼야
            {"조문번호": "2", "조문여부": "조문", "조문키": "0002001", "조문제목": "정의",
             "조문내용": "제2조(정의)",
             "항": [{"항번호": "①", "항내용": "① 용어의 뜻은 다음과 같다.",
                    "호": [{"호내용": "6. \"거실\"이란 ... 방을 말한다."}]}]},
            {"조문번호": "4", "조문가지번호": "2", "조문여부": "조문", "조문키": "0004021",
             "조문제목": "건축위원회 심의", "조문내용": "제4조의2(건축위원회 심의) 내용"},
            {"조문번호": "5", "조문가지번호": "0", "조문여부": "조문", "조문키": "0005001",
             "조문내용": "제5조 단일조문"},
        ]},
    }
}


def test_meta():
    meta, _ = parse_law_service(_FIXTURE)
    assert meta.law_id == "001823" and meta.statute == "건축법"
    assert meta.eff_date == "20260227" and meta.ministry == "국토교통부"


def test_jeonmun_filtered_out():
    """조문여부=='전문'(장 제목)은 제외."""
    _, arts = parse_law_service(_FIXTURE)
    assert len(arts) == 3                      # 4개 중 전문 1개 제외
    assert all(a.article_no != 1 for a in arts)


def test_branch_number_parsed():
    _, arts = parse_law_service(_FIXTURE)
    a42 = next(a for a in arts if a.article_no == 4)
    assert a42.branch_no == 2                   # 제4조의2
    a5 = next(a for a in arts if a.article_no == 5)
    assert a5.branch_no is None                 # 가지번호 "0" → None


def test_flatten_includes_ho():
    _, arts = parse_law_service(_FIXTURE)
    a2 = next(a for a in arts if a.article_no == 2)
    assert "거실" in a2.text and "방을 말한다" in a2.text   # 항/호 평탄화 포함


def test_flatten_empty_unit():
    assert flatten_article({}) == ""


def _one_unit_law(unit, ministry="국토교통부"):
    return {"법령": {"기본정보": {"법령ID": "1", "법령명_한글": "x", "시행일자": "20260101",
            "소관부처": ministry}, "조문": {"조문단위": [unit]}}}


def test_mok_4level_flattened_and_str_ministry():
    """목(4단계) 평탄화 + 소관부처가 dict 아닌 str인 분기."""
    unit = {"조문번호": "49", "조문여부": "조문", "조문키": "x", "조문내용": "제49조",
            "항": [{"항내용": "① 본문", "호": [{"호내용": "1. 호본문",
                   "목": [{"목내용": "가. 목본문"}]}]}]}
    meta, arts = parse_law_service(_one_unit_law(unit, ministry="국토교통부"))
    assert "가. 목본문" in arts[0].text        # 목 4단계 포함
    assert meta.ministry == "국토교통부"        # str 부처 분기


def test_non_integer_article_number_skipped():
    """조문번호가 비정수면 skip(except 경로)."""
    _, arts = parse_law_service(_one_unit_law({"조문번호": "부칙", "조문여부": "조문", "조문내용": "x"}))
    assert arts == []


_LSSTMD = {"법령체계도": {"상하위법": {"법률": {
    "기본정보": {"법령ID": "001823", "법령명": "건축법", "법종구분": {"content": "법률"}},
    "대통령령": {"기본정보": {"법령ID": "002118", "법령명": "건축법 시행령"}},
    "부령": {"기본정보": {"법령ID": "006191", "법령명": "건축법 시행규칙"}},
    "자치법규": {"조례": [{"기본정보": {"자치법규ID": "999", "자치법규명": "가평군 건축 조례"}}]},
    "기타령": {"기본정보": {"법령ID": "008570", "법령명": "표준설계도서 등의 운영에 관한 규칙"}},
}}}}


def test_parse_hierarchy_children():
    """트리 구조 기반: 법령ID 보유 위임 하위 전부(이름 prefix 무관). 조례·base·상위법률 제외."""
    out = dict(parse_hierarchy(_LSSTMD, "건축법", "001823"))
    # 시행령·시행규칙 + 이름이 '건축법'으로 시작 안 하는 위임 부령(008570)도 포함(누락 해소)
    assert out == {"002118": "건축법 시행령", "006191": "건축법 시행규칙",
                   "008570": "표준설계도서 등의 운영에 관한 규칙"}
    assert "999" not in out and "001823" not in out   # 조례(법령ID 없음)·base 제외
