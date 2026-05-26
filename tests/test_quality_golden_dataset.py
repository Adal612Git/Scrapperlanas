from __future__ import annotations

import json
from pathlib import Path

import pytest

from scrapperlanas.services.quality import assess_opportunity_quality


FIXTURE_PATH = Path(__file__).parent / "fixtures" / "opportunity_quality_golden.json"
GRADE_RANK = {"REJECTED": 0, "D": 1, "C": 2, "B": 3, "A2": 4, "A1": 5}


def _cases() -> list[dict]:
    return json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))


def test_golden_dataset_has_minimum_case_count():
    assert len(_cases()) >= 40


@pytest.mark.parametrize("case", _cases(), ids=lambda case: case["name"])
def test_opportunity_quality_golden_dataset(case: dict):
    quality = assess_opportunity_quality(case["input"])
    expected = case["expected"]

    assert quality.quality_stage == expected["expected_quality_stage"]
    assert quality.commercial_intent_label == expected["expected_commercial_intent_label"]
    assert quality.is_contactable is expected["expected_contactable"]
    assert quality.budget.is_valid_commercial_budget is expected["expected_budget_valid"]
    assert GRADE_RANK[quality.grade] <= GRADE_RANK[expected["expected_grade_max"]]


def test_golden_dataset_reddit_garbage_is_never_contactable():
    cases = _cases()
    garbage_names = {"nosleep fiction warehouse basement", "conspiracy directed energy weapon"}
    for case in cases:
        if case["name"] in garbage_names:
            quality = assess_opportunity_quality(case["input"])
            assert quality.quality_stage == "REJECTED_NOISE"
            assert quality.is_contactable is False
            assert quality.final_score_cap <= 10
