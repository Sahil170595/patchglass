"""HIDDEN pass2pass: passes both before and after the golden fix (must not regress)."""

from widget.core import normalize


def test_strips():
    assert normalize("  hi  ") == "hi"
