"""scale_run_script parser: reads the already-normalized in-container `output.json`.

SWE-bench Pro's per-instance parser.py runs *inside* the container and emits
`{"tests": [{"name", "status"}]}` with status already in our vocabulary. This parser just maps
name->status and applies MISSING for absent expected ids — no regex, the framework knowledge lives
in the container script.
"""

from __future__ import annotations

import json
from typing import Final

from taskbundle.errors import GradeError
from taskbundle.parsers.base import RunOutput, TestStatus

NAME: Final = "scale_run_script"

_ARTIFACT_KEY: Final = "output.json"

# The in-container parser already emits our vocabulary; validate it stays within the enum.
_ALLOWED: Final = {
    s.value for s in (TestStatus.PASSED, TestStatus.FAILED, TestStatus.SKIPPED, TestStatus.ERROR, TestStatus.XFAIL)
}


def _load(artifacts: dict[str, str]) -> dict[str, TestStatus]:
    raw = artifacts.get(_ARTIFACT_KEY)
    if raw is None:
        raise GradeError(f"scale_run_script parser requires artifact {_ARTIFACT_KEY!r}; none found")
    try:
        doc = json.loads(raw)
        tests = doc["tests"]
    except (json.JSONDecodeError, KeyError, TypeError) as exc:
        raise GradeError(f"scale_run_script {_ARTIFACT_KEY!r} is malformed: {exc}") from exc
    result: dict[str, TestStatus] = {}
    for entry in tests:
        try:
            name = entry["name"]
            status = entry["status"]
        except (KeyError, TypeError) as exc:
            raise GradeError(f"scale_run_script entry missing name/status: {entry!r}") from exc
        if status not in _ALLOWED:
            raise GradeError(f"scale_run_script: unknown status {status!r} for {name!r}")
        result[name] = TestStatus(status)
    return result


class ScaleRunScriptParser:
    """Map an in-container output.json {tests:[{name,status}]} to a per-id status map."""

    name = NAME

    def parse(self, output: RunOutput, expected_ids: list[str]) -> dict[str, TestStatus]:
        found = _load(output.artifacts)
        return {tid: found.get(tid, TestStatus.MISSING) for tid in expected_ids}


parser: Final = ScaleRunScriptParser()
