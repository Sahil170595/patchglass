"""Parser seam + the normalized status vocabulary.

Contract: parse(output, expected_ids) -> {test_id: TestStatus}. `output` carries stdout/stderr AND
optional structured artifacts (so a parser that loads an in-container output.json fits the same seam).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Protocol, runtime_checkable

# Strip ANSI escapes and carriage returns before matching (robustness across runners).
_ANSI_RE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]|\r")
# Strip pytest timing annotations like " (0.12s)" / "[ 12%]" before nodeid comparison (id-drift fix).
_TIMING_RE = re.compile(r"\s*\[\s*\d+%\s*\]|\s*\(\d+\.\d+s\)")


class TestStatus(StrEnum):
    """Normalized per-test outcome vocabulary (superset across frameworks)."""

    __test__ = False  # this StrEnum's name starts with "Test"; tell pytest it is not a test class.

    PASSED = "PASSED"
    FAILED = "FAILED"
    SKIPPED = "SKIPPED"
    ERROR = "ERROR"
    XFAIL = "XFAIL"
    MISSING = "MISSING"  # expected id never appeared in output (edge-case: id drift / collection error).


@dataclass
class RunOutput:
    """Raw result of running the test command inside the container."""

    stdout: str
    stderr: str = ""
    exit_code: int = 0
    artifacts: dict[str, str] = field(default_factory=dict)  # e.g. {"output.json": "..."} for in-container parsers.


def normalize(text: str) -> str:
    """Strip ANSI + timing annotations so nodeid matching is stable."""
    return _TIMING_RE.sub("", _ANSI_RE.sub("", text))


@runtime_checkable
class Parser(Protocol):
    """Turn raw runner output into a per-test status map for the expected ids."""

    name: str

    def parse(self, output: RunOutput, expected_ids: list[str]) -> dict[str, TestStatus]:
        """Return {test_id: status} for every id in expected_ids (MISSING if not found)."""
        ...
