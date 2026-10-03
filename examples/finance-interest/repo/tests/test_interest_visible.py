"""A VISIBLE test (in the repo the solver sees). Passes on both buggy and fixed code."""

from ledger.interest import simple_interest


def test_simple_interest_visible():
    assert simple_interest(100, 0.05, 1) == 105.00
