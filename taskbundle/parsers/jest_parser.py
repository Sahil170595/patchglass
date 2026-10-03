"""Jest parser: prefers a `jest --json` artifact (assertionResults), falls back to the ✓/✕/○ text.

Test id = the assertion `fullName` (describe-block path + test title), matching jest --json.
"""

from __future__ import annotations

import json
import re
from typing import Final

from taskbundle.errors import GradeError
from taskbundle.parsers.base import RunOutput, TestStatus, normalize

NAME: Final = "jest"

_JSON_ARTIFACT_KEYS: Final = ("jest.json", "report.json", "output.json")

# jest --json assertion status -> normalized status. "todo"/"pending" => SKIPPED.
_JSON_STATUS: Final = {
    "passed": TestStatus.PASSED,
    "failed": TestStatus.FAILED,
    "pending": TestStatus.SKIPPED,
    "skipped": TestStatus.SKIPPED,
    "todo": TestStatus.SKIPPED,
    "disabled": TestStatus.SKIPPED,
}

# Text fallback (jest default reporter): "  ✓ name (3 ms)", "  ✕ name", "  ○ skipped name".
_PASS_RE: Final = re.compile(r"^\s*[✓√]\s+(?P<id>.+?)(?:\s+\(\d+\s*ms\))?\s*$", re.MULTILINE)
_FAIL_RE: Final = re.compile(r"^\s*[✕×✗]\s+(?P<id>.+?)(?:\s+\(\d+\s*ms\))?\s*$", re.MULTILINE)
_SKIP_RE: Final = re.compile(r"^\s*[○◯]\s+skipped\s+(?P<id>.+?)\s*$", re.MULTILINE)


def _load_json(artifacts: dict[str, str]) -> dict[str, TestStatus] | None:
    for key in _JSON_ARTIFACT_KEYS:
        raw = artifacts.get(key)
        if raw is None:
            continue
        try:
            doc = json.loads(raw)
            suites = doc["testResults"]
        except (json.JSONDecodeError, KeyError, TypeError) as exc:
            raise GradeError(f"jest json artifact {key!r} is malformed: {exc}") from exc
        result: dict[str, TestStatus] = {}
        for suite in suites:
            for assertion in suite.get("assertionResults", []):
                try:
                    name = assertion["fullName"]
                    raw_status = assertion["status"]
                except (KeyError, TypeError) as exc:
                    raise GradeError(f"jest assertion missing fullName/status: {assertion!r}") from exc
                status = _JSON_STATUS.get(raw_status)
                if status is None:
                    raise GradeError(f"jest: unknown assertion status {raw_status!r} for {name!r}")
                result[name] = status
        return result
    return None


def _scan_text(text: str) -> dict[str, TestStatus]:
    found: dict[str, TestStatus] = {}
    for match in _SKIP_RE.finditer(text):
        found[match.group("id")] = TestStatus.SKIPPED
    for match in _PASS_RE.finditer(text):
        found[match.group("id")] = TestStatus.PASSED
    for match in _FAIL_RE.finditer(text):
        found[match.group("id")] = TestStatus.FAILED
    return found


class JestParser:
    """Parse Jest output (JSON preferred) into a per-id status map."""

    name = NAME

    def parse(self, output: RunOutput, expected_ids: list[str]) -> dict[str, TestStatus]:
        found = _load_json(output.artifacts)
        if found is None:
            found = _scan_text(normalize(output.stdout))
        return {tid: found.get(tid, TestStatus.MISSING) for tid in expected_ids}


parser: Final = JestParser()
