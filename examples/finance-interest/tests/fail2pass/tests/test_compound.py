"""HIDDEN fail2pass: fails on the buggy baseline (simple interest), passes once compounding is fixed."""

from ledger.interest import compound_interest


def test_compounds_annually():
    assert compound_interest(1000, 0.05, 2) == 1102.50
    assert compound_interest(1000, 0.10, 3) == 1331.00
