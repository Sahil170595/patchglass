"""HIDDEN pass2pass: a legitimate path inside base_dir is still allowed; passes before AND after."""

from safeio.paths import safe_join


def test_allows_nested_file():
    assert safe_join("/srv/data", "sub/report.csv") == "/srv/data/sub/report.csv"
