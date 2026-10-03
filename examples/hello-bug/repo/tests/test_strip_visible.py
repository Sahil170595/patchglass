"""A VISIBLE test (lives in the repo the solver sees). Passes on both buggy and fixed code."""

from widget.core import normalize


def test_visible_strip():
    assert normalize("  hi  ") == "hi"
