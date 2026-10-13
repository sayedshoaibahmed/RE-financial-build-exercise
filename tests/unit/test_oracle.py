import pytest

from tests import oracle


@pytest.fixture(scope="module")
def o():
    return oracle.scan()


def test_oracle_finds_periods_in_header_order(o):
    assert len(o["periods"]) == len(set(o["periods"])) > 0
    assert o["periods"][0] > o["periods"][-1]  # newest first in this workbook


def test_oracle_leaves_are_unique_and_exclude_totals(o):
    labels = o["leaf_labels"]
    assert len(labels) == len(set(labels))
    assert not [l for l in labels if l.startswith("Total")]
    assert o["subtotals"] and o["derived"]


def test_oracle_one_time_sanity_against_plan_inventory(o):
    # One-off check that the oracle agrees with PLAN.md section 3.2 (not a pin for other tests).
    assert len(o["periods"]) == 18
    assert len(o["leaf_labels"]) == 83
    assert len(o["slash_labels"]) == 9
    assert {r for r, _ in o["subtotals"]} >= {59, 71}
    assert len(o["all_zero_leaves"]) >= 4
