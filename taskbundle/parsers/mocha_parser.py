"""Mocha parser: reads `mocha --reporter json` (passes/failures/pending arrays).

For benchmark-compatible identifiers, mocha test ids are "{file} | {fullTitle}" — the JSON reporter
emits both `file` and `fullTitle` per test, and they are joined to disambiguate same-titled tests
across files.
"""

from __future__ import annotations

import json
from typing import Final

from taskbundle.errors import GradeError
from taskbundle.parsers.base import RunOutput, TestStatus

NAME: Final = "mocha"

_JSON_ARTIFACT_KEYS: Final = ("mocha.json", "report.json", "output.json")

# Mocha JSON reporter top-level arrays -> status for every test therein.
_BUCKET_STATUS: Final = {
    "passes": TestStatus.PASSED,
    "failures": TestStatus.FAILED,
    "pending": TestStatus.SKIPPED,
}

_ID_SEP: Final = " | "  # "{file} | {fullTitle}" join (SWE-bench Pro convention).


def _load_json(artifacts: dict[str, str]) -> dict[str, TestStatus]:
    for key in _JSON_ARTIFACT_KEYS:
        raw = artifacts.get(key)
        if raw is None:
            continue
        try:
            doc = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise GradeError(f"mocha json artifact {key!r} is malformed: {exc}") from exc
        if not isinstance(doc, dict):
            raise GradeError(f"mocha json artifact {key!r} is not an object")
        result: dict[str, TestStatus] = {}
        for bucket, status in _BUCKET_STATUS.items():
            for test in doc.get(bucket, []):
                try:
                    title = test["fullTitle"]
                except (KeyError, TypeError) as exc:
                    raise GradeError(f"mocha {bucket} entry missing fullTitle: {test!r}") from exc
                file_part = test.get("file", "")
                test_id = f"{file_part}{_ID_SEP}{title}" if file_part else title
                result[test_id] = status
        return result
    raise GradeError(f"mocha parser requires a JSON-reporter artifact (one of {list(_JSON_ARTIFACT_KEYS)}); none found")


class MochaParser:
    """Parse `mocha --reporter json` output into a per-id status map."""

    name = NAME

    def parse(self, output: RunOutput, expected_ids: list[str]) -> dict[str, TestStatus]:
        if not expected_ids:
            return {}
        found = _load_json(output.artifacts)
        return {tid: found.get(tid, TestStatus.MISSING) for tid in expected_ids}


parser: Final = MochaParser()
