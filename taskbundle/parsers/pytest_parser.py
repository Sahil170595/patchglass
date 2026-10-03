"""pytest parser: prefers a `pytest --json-report` artifact, falls back to `-rA`/verbose text.

Text supports both orderings emitted across pytest versions/plugins:
  "PASSED nodeid"   (status-first, `-rA` summary)
  "nodeid PASSED"   (status-trailing, `-v` progress line)
"""

from __future__ import annotations

import json
import re
from typing import Final

from taskbundle.errors import GradeError
from taskbundle.parsers.base import RunOutput, TestStatus, normalize

NAME: Final = "pytest"

# Keys under which a `pytest --json-report` artifact may be stashed by the runner.
_JSON_ARTIFACT_KEYS: Final = ("report.json", "output.json", "pytest.json", ".report.json")

# Text-status token -> normalized status. ERROR before the loop so longest tokens win cleanly.
_TEXT_STATUS: Final = {
    "PASSED": TestStatus.PASSED,
    "FAILED": TestStatus.FAILED,
    "ERROR": TestStatus.ERROR,
    "SKIPPED": TestStatus.SKIPPED,
    "XFAIL": TestStatus.XFAIL,
    "XPASS": TestStatus.PASSED,  # unexpectedly-passing xfail: counts as a pass for grading.
}

# `pytest-json-report` outcome string -> normalized status.
_JSON_OUTCOME: Final = {
    "passed": TestStatus.PASSED,
    "failed": TestStatus.FAILED,
    "skipped": TestStatus.SKIPPED,
    "error": TestStatus.ERROR,
    "xfailed": TestStatus.XFAIL,
    "xpassed": TestStatus.PASSED,
}

_STATUS_ALT: Final = "|".join(_TEXT_STATUS)
# Status-first: "PASSED tests/x.py::test_a" or "-rA" failure form "FAILED tests/x.py::test_a - reason".
# The id is non-greedy and an optional " - <reason>" tail is dropped (real `pytest -rA` appends one on
# failures/errors); nodeids never contain " - " (space-dash-space), so this never truncates a real id.
_STATUS_FIRST_RE: Final = re.compile(rf"^(?P<status>{_STATUS_ALT})\s+(?P<id>\S.*?)(?:\s+-\s.*)?\s*$", re.MULTILINE)
# Status-trailing: "tests/x.py::test_a PASSED"; require a nodeid-ish token (has '::' or a path sep).
_STATUS_TRAILING_RE: Final = re.compile(rf"^(?P<id>\S*(?:::|/)\S*)\s+(?P<status>{_STATUS_ALT})\b.*$", re.MULTILINE)


def _load_json_report(artifacts: dict[str, str]) -> dict[str, TestStatus] | None:
    """Return id->status from a json-report artifact, or None if no such artifact is present."""
    for key in _JSON_ARTIFACT_KEYS:
        raw = artifacts.get(key)
        if raw is None:
            continue
        try:
            doc = json.loads(raw)
            tests = doc["tests"]
        except (json.JSONDecodeError, KeyError, TypeError) as exc:
            raise GradeError(f"pytest json-report artifact {key!r} is malformed: {exc}") from exc
        result: dict[str, TestStatus] = {}
        for entry in tests:
            try:
                nodeid = entry["nodeid"]
                outcome = entry["outcome"]
            except (KeyError, TypeError) as exc:
                raise GradeError(f"pytest json-report entry missing nodeid/outcome: {entry!r}") from exc
            status = _JSON_OUTCOME.get(outcome)
            if status is None:
                raise GradeError(f"pytest json-report: unknown outcome {outcome!r} for {nodeid!r}")
            result[nodeid] = status
        return result
    return None


def _scan_text(text: str) -> dict[str, TestStatus]:
    """Build id->status from verbose/-rA text. Last write per id wins (final summary lines)."""
    found: dict[str, TestStatus] = {}
    for match in _STATUS_FIRST_RE.finditer(text):
        found[match.group("id")] = _TEXT_STATUS[match.group("status")]
    for match in _STATUS_TRAILING_RE.finditer(text):
        found[match.group("id")] = _TEXT_STATUS[match.group("status")]
    return found


class PytestParser:
    """Parse pytest output (JSON report preferred) into a per-id status map."""

    name = NAME

    def parse(self, output: RunOutput, expected_ids: list[str]) -> dict[str, TestStatus]:
        found = _load_json_report(output.artifacts)
        if found is None:
            found = _scan_text(normalize(output.stdout))
        return {tid: found.get(tid, TestStatus.MISSING) for tid in expected_ids}


parser: Final = PytestParser()
