"""A VISIBLE test (in the repo the solver sees). Passes on both buggy and fixed code."""

from safeio.paths import safe_join


def test_allows_normal_file():
    assert safe_join("/srv/data", "report.csv") == "/srv/data/report.csv"
