"""Django/unittest parser: "test_x (module.Class) ... ok|FAIL|ERROR|skipped" lines.

The unittest TextTestRunner (Django's default) prints the test id, " ... ", then the outcome word.
"""

from __future__ import annotations

import re
from typing import Final

from taskbundle.parsers.base import RunOutput, TestStatus, normalize

NAME: Final = "django"

# Outcome word (case-insensitive) -> status. "expected failure" is unittest's xfail phrasing.
_WORD_STATUS: Final = {
    "ok": TestStatus.PASSED,
    "fail": TestStatus.FAILED,
    "error": TestStatus.ERROR,
    "skipped": TestStatus.SKIPPED,
    "expected failure": TestStatus.XFAIL,
    "unexpected success": TestStatus.PASSED,
}

# id like "test_x (module.Class)" or "test_x (module.Class.test_x)", then " ... <outcome>[ reason]".
_LINE_RE: Final = re.compile(
    r"^(?P<id>\S+ \([^)]+\))\s+\.\.\.\s+" r"(?P<word>ok|FAIL|ERROR|skipped|expected failure|unexpected success)\b",
    re.MULTILINE | re.IGNORECASE,
)


def _scan(text: str) -> dict[str, TestStatus]:
    found: dict[str, TestStatus] = {}
    for match in _LINE_RE.finditer(text):
        found[match.group("id")] = _WORD_STATUS[match.group("word").lower()]
    return found


class DjangoParser:
    """Parse Django/unittest TextTestRunner output into a per-id status map."""

    name = NAME

    def parse(self, output: RunOutput, expected_ids: list[str]) -> dict[str, TestStatus]:
        found = _scan(normalize(output.stdout))
        return {tid: found.get(tid, TestStatus.MISSING) for tid in expected_ids}


parser: Final = DjangoParser()
