from __future__ import annotations

import pytest

from daily_review.review_normalizer import NormalizationError, normalize_review


DAY = "2026-07-15"


def test_headings_bullets_colons_variants_and_journal():
    text = """今日の振り返り
今日できたこと：
- 開発
* 筋トレ
未完了:
・院試
崩れた原因
1. 眠気
明日のMain
- 過去問
最低ライン：
- 1問
日記
日本語の日記
改行も保持"""
    result = normalize_review(text, effective_date=DAY)
    value = result["normalized"]
    assert value["done"] == ["開発", "筋トレ"]
    assert value["not_done"] == ["院試"]
    assert value["causes"] == ["眠気"]
    assert value["tomorrow"] == ["過去問"]
    assert value["minimum"] == ["1問"]
    assert value["journal"] == "日本語の日記\n改行も保持"
    assert result["confidence"]["overall"] == "high"


def test_free_text_negation_minimum_unclassified_and_warning():
    result = normalize_review(
        "院試勉強はできなかった\n最低限は1問\n最悪でもスクワット50回\n意味を決めつけない文章",
        effective_date=DAY,
    )
    value = result["normalized"]
    assert value["not_done"] == ["院試勉強はできなかった"]
    assert value["minimum"] == ["1問", "スクワット50回"]
    assert value["unclassified"] == ["意味を決めつけない文章"]
    assert {item["code"] for item in result["warnings"]} >= {
        "RULE_BASED_NEGATION",
        "AMBIGUOUS_MINIMUM",
        "UNCLASSIFIED_TEXT",
    }
    assert result["raw_input"].endswith("意味を決めつけない文章")


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("昨日の振り返り\nメモ\n昨日", "2026-07-14"),
        ("明日の振り返り\nメモ\n明日", "2026-07-16"),
        ("2026/02/29の振り返り\nメモ\n閏日", None),
        ("2024/02/29の振り返り\nメモ\n閏日", "2024-02-29"),
    ],
)
def test_relative_explicit_and_invalid_dates(text, expected):
    if expected is None:
        with pytest.raises(NormalizationError):
            normalize_review(text, effective_date=DAY)
    else:
        assert (
            normalize_review(text, effective_date=DAY)["normalized"]["date"] == expected
        )


def test_empty_whitespace_unicode_and_size_boundaries():
    with pytest.raises(NormalizationError):
        normalize_review(" \n\t", effective_date=DAY)
    assert (
        "🎉"
        in normalize_review("メモ\n🎉", effective_date=DAY)["normalized"]["journal"]
    )
    normalize_review("a" * 20_000, effective_date=DAY)
    with pytest.raises(NormalizationError):
        normalize_review("a" * 20_001, effective_date=DAY)
