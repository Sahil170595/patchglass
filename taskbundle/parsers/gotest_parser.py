"""go test parser: "--- PASS|FAIL|SKIP: TestName (0.00s)" lines (incl. subtests "Test/case")."""

from __future__ import annotations

import re
from typing import Final

from taskbundle.parsers.base import RunOutput, TestStatus, normalize

NAME: Final = "go"

_WORD_STATUS: Final = {
    "PASS": TestStatus.PASSED,
    "FAIL": TestStatus.FAILED,
    "SKIP": TestStatus.SKIPPED,
}

# go test -v: "--- PASS: TestTable/case_one (0.00s)". id may contain '/' for subtests.
# normalize() strips the trailing "(0.00s)" timing annotation, so the duration is optional here.
_LINE_RE: Final = re.compile(r"^\s*--- (?P<word>PASS|FAIL|SKIP):\s+(?P<id>\S+)(?:\s+\([0-9.]+s\))?\s*$", re.MULTILINE)


def _scan(text: str) -> dict[str, TestStatus]:
    found: dict[str, TestStatus] = {}
    for match in _LINE_RE.finditer(text):
        found[match.group("id")] = _WORD_STATUS[match.group("word")]
    return found


class GoTestParser:
    """Parse `go test -v` output into a per-id status map."""

    name = NAME

    def parse(self, output: RunOutput, expected_ids: list[str]) -> dict[str, TestStatus]:
        found = _scan(normalize(output.stdout))
        return {tid: found.get(tid, TestStatus.MISSING) for tid in expected_ids}


parser: Final = GoTestParser()
