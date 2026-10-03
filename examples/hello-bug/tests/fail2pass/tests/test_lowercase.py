"""HIDDEN fail2pass: fails on the buggy baseline (no lowercasing), passes after the golden fix."""

from widget.core import normalize


def test_lowercases():
    assert normalize("  HELLO  ") == "hello"
