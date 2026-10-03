"""HIDDEN pass2pass: simple_interest is unaffected by the fix; passes before AND after."""

from ledger.interest import simple_interest


def test_simple_interest_unchanged():
    assert simple_interest(1000, 0.05, 2) == 1100.00
    assert simple_interest(500, 0.10, 3) == 650.00
