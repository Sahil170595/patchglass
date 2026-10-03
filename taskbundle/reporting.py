"""Verdict + report layer — pure logic, no docker.

Correctness-critical: this is where `resolved` is computed. The verdict is
**conjunctive** (SWE-bench / SWE-bench Pro semantics): a patch resolves a task iff it was
applied AND every fail2pass test now passes AND every pass2pass test still passes. A test
"passes" iff its normalized status is PASSED or XFAIL; everything else (FAILED, SKIPPED,
ERROR, MISSING) is a non-pass. The four transition buckets (FAIL_TO_PASS / PASS_TO_PASS /
FAIL_TO_FAIL / PASS_TO_FAIL) are diagnostic; the verdict depends only on the after-state.
"""

from __future__ import annotations

from pathlib import Path
from typing import Final

from pydantic import BaseModel, Field

from taskbundle import constants
from taskbundle.errors import GradeError
from taskbundle.parsers.base import TestStatus

# Statuses that count as a passing test. XFAIL = expected-fail that did fail as expected, which
# the SWE-bench convention treats as a pass for grading. Everything else is a non-pass.
PASSING_STATUSES: Final[frozenset[TestStatus]] = frozenset({TestStatus.PASSED, TestStatus.XFAIL})

_ID_COL_WIDTH: Final = 6  # column label width in render_table (aligns the bucket labels).


def _passed(status: TestStatus) -> bool:
    """A test passed iff its status is PASSED or XFAIL (SWE-bench grading convention)."""
    return status in PASSING_STATUSES


class Group(BaseModel):
    """A partition of test ids into those that passed (`success`) and those that did not."""

    success: list[str] = Field(default_factory=list)
    failure: list[str] = Field(default_factory=list)


class TransitionBuckets(BaseModel):
    """Per-bucket transition partition (after-state, plus baseline→after deltas when known).

    `fail_to_pass` / `pass_to_pass` are derived from the after-state alone and drive the verdict.
    `fail_to_fail` / `pass_to_fail` are baseline-relative diagnostics (empty when no baseline given).
    """

    fail_to_pass: Group = Field(default_factory=Group)
    pass_to_pass: Group = Field(default_factory=Group)
    fail_to_fail: Group = Field(default_factory=Group)
    pass_to_fail: Group = Field(default_factory=Group)


class RunReport(BaseModel):
    """Structured grading verdict for one run — the JSON eval artifact (`run.json`)."""

    schema_version: int = constants.SCHEMA_VERSION
    run_id: str | None = None
    solver: str = ""  # the solver spec that produced this run (e.g. 'golden', 'llm:openai/gpt-4o') — self-id.
    image_digest: str = ""  # the resolved sha256 the verdict was produced against (reproducibility anchor).
    config_hash: str = ""  # sha256 over the BUNDLE inputs (digest, commit, cmd, buckets) — machine-independent.
    deps_hash: str = ""  # sha256 of the resolved deps.lock — the ACTUAL installed set, for drift detection.
    resolved: bool
    patch_exists: bool
    patch_applied: bool
    patch_is_none: bool
    buckets: TransitionBuckets
    per_test: dict[str, str] = Field(default_factory=dict)  # id -> TestStatus.value
    cost: dict[str, float] = Field(default_factory=dict)  # tokens / wall-clock / retries sidecar.
    warnings: list[str] = Field(default_factory=list)  # non-fatal advisories (e.g. solver touched test infra).


def _status_for(outcomes: dict[str, TestStatus], test_id: str) -> TestStatus:
    """Look up a test's after-status, treating an absent id as MISSING (edge-case #2)."""
    return outcomes.get(test_id, TestStatus.MISSING)


def _partition(ids: list[str], outcomes: dict[str, TestStatus]) -> Group:
    """Split `ids` into pass/non-pass groups, preserving input order (stable, auditable)."""
    group = Group()
    for test_id in ids:
        if _passed(_status_for(outcomes, test_id)):
            group.success.append(test_id)
        else:
            group.failure.append(test_id)
    return group


def _baseline_transition(
    ids: list[str],
    baseline: dict[str, TestStatus],
    after: dict[str, TestStatus],
    *,
    baseline_was_passing: bool,
) -> Group:
    """Partition ids by *after* state, restricted to those whose baseline matched the given polarity.

    `baseline_was_passing=False` → FAIL_TO_* ids (baseline non-pass); True → PASS_TO_* ids.
    """
    group = Group()
    for test_id in ids:
        if _passed(_status_for(baseline, test_id)) != baseline_was_passing:
            continue
        if _passed(_status_for(after, test_id)):
            group.success.append(test_id)
        else:
            group.failure.append(test_id)
    return group


def compute_report(
    outcomes: dict[str, TestStatus],
    pass2pass: list[str],
    fail2pass: list[str],
    *,
    patch_exists: bool,
    patch_applied: bool,
    patch_is_none: bool,
    baseline: dict[str, TestStatus] | None = None,
) -> RunReport:
    """Compute the structured verdict from after-state test outcomes.

    resolved = patch_applied AND every fail2pass passed AND every pass2pass passed (and the patch
    was a real, applied patch — `patch_is_none` or `not patch_applied` force resolved=False).
    """
    f2p = _partition(fail2pass, outcomes)
    p2p = _partition(pass2pass, outcomes)

    buckets = TransitionBuckets(fail_to_pass=f2p, pass_to_pass=p2p)
    if baseline is not None:
        # FAIL_TO_FAIL: was non-pass at baseline, still non-pass. PASS_TO_FAIL: regressed.
        buckets.fail_to_fail = _baseline_transition(
            fail2pass + pass2pass, baseline, outcomes, baseline_was_passing=False
        )
        buckets.pass_to_fail = _baseline_transition(
            fail2pass + pass2pass, baseline, outcomes, baseline_was_passing=True
        )

    resolved = patch_applied and not patch_is_none and not f2p.failure and not p2p.failure

    per_test = {tid: _status_for(outcomes, tid).value for tid in (*fail2pass, *pass2pass)}

    return RunReport(
        resolved=resolved,
        patch_exists=patch_exists,
        patch_applied=patch_applied,
        patch_is_none=patch_is_none,
        buckets=buckets,
        per_test=per_test,
    )


def render_table(report: RunReport) -> str:
    """Compact human-readable verdict: RESOLVED/NOT line, per-bucket counts, failing ids."""
    verdict = "RESOLVED" if report.resolved else "NOT RESOLVED"
    run = report.run_id or "(unsaved)"
    lines = [
        f"verdict: {verdict}",
        f"run: {run}  patch_applied={report.patch_applied} patch_is_none={report.patch_is_none}",
    ]

    b = report.buckets
    rows: list[tuple[str, Group]] = [
        ("F2P", b.fail_to_pass),
        ("P2P", b.pass_to_pass),
        ("F2F", b.fail_to_fail),
        ("P2F", b.pass_to_fail),
    ]
    for label, group in rows:
        total = len(group.success) + len(group.failure)
        if total == 0:
            continue
        lines.append(f"  {label:<{_ID_COL_WIDTH}} pass {len(group.success)}/{total}")

    failing = b.fail_to_pass.failure + b.pass_to_pass.failure
    if failing:
        lines.append("failing:")
        lines.extend(f"  - {tid}" for tid in failing)

    return "\n".join(lines)


def write_run_json(report: RunReport, path: Path) -> None:
    """Serialize the report to `path` as indented JSON (the run's eval artifact)."""
    try:
        path.write_text(report.model_dump_json(indent=constants.JSON_INDENT), encoding="utf-8")
    except OSError as exc:
        raise GradeError(f"could not write run.json to {path}: {exc}") from exc
