"""ansible-test units parser: xdist "[gwN] [ NN%] STATUS test/units/...::..." lines.

`ansible-test units` runs under pytest-xdist; each worker prefixes its line with "[gwN]" and a
percent, then the status and the pytest nodeid.
"""

from __future__ import annotations

import re
from typing import Final

from taskbundle.parsers.base import RunOutput, TestStatus, normalize

NAME: Final = "ansible"

_WORD_STATUS: Final = {
    "PASSED": TestStatus.PASSED,
    "FAILED": TestStatus.FAILED,
    "SKIPPED": TestStatus.SKIPPED,
    "ERROR": TestStatus.ERROR,
    "XFAIL": TestStatus.XFAIL,
}

_STATUS_ALT: Final = "|".join(_WORD_STATUS)
# Percent "[ NN%]" is stripped by normalize(); keep it optional so the regex is robust either way.
_LINE_RE: Final = re.compile(
    rf"^\[gw\d+\]\s*(?:\[\s*\d+%\s*\]\s*)?(?P<status>{_STATUS_ALT})\s+(?P<id>\S+)",
    re.MULTILINE,
)


def _scan(text: str) -> dict[str, TestStatus]:
    found: dict[str, TestStatus] = {}
    for match in _LINE_RE.finditer(text):
        found[match.group("id")] = _WORD_STATUS[match.group("status")]
    return found


class AnsibleParser:
    """Parse `ansible-test units` (xdist) output into a per-id status map."""

    name = NAME

    def parse(self, output: RunOutput, expected_ids: list[str]) -> dict[str, TestStatus]:
        found = _scan(normalize(output.stdout))
        return {tid: found.get(tid, TestStatus.MISSING) for tid in expected_ids}


parser: Final = AnsibleParser()
