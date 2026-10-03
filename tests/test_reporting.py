"""TDD spec for the verdict/report layer. Pure logic — no docker, fast.

Encodes the correctness-critical `resolved` semantics:
resolved iff patch applied AND every fail2pass PASSED AND every pass2pass PASSED.
"""

from __future__ import annotations

import json
from pathlib import Path

from taskbundle import constants
from taskbundle.parsers.base import TestStatus
from taskbundle.reporting import (
    Group,
    RunReport,
    TransitionBuckets,
    compute_report,
    render_table,
    write_run_json,
)

F2P = "tests/t_feature.py::test_new_behavior"
F2P_2 = "tests/t_feature.py::test_other_new_behavior"
P2P = "tests/t_core.py::test_existing"
P2P_2 = "tests/t_core.py::test_existing_two"


def _applied(outcomes: dict[str, TestStatus]) -> RunReport:
    """compute_report for the common 'patch present and applied' case."""
    return compute_report(
        outcomes,
        pass2pass=[P2P, P2P_2],
        fail2pass=[F2P, F2P_2],
        patch_exists=True,
        patch_applied=True,
        patch_is_none=False,
    )


# --- resolved == True path -----------------------------------------------------------------


def test_all_pass_resolves() -> None:
    report = _applied(
        {
            F2P: TestStatus.PASSED,
            F2P_2: TestStatus.PASSED,
            P2P: TestStatus.PASSED,
            P2P_2: TestStatus.PASSED,
        }
    )
    assert report.resolved is True
    assert report.buckets.fail_to_pass.success == [F2P, F2P_2]
    assert report.buckets.fail_to_pass.failure == []
    assert report.buckets.pass_to_pass.success == [P2P, P2P_2]
    assert report.buckets.pass_to_pass.failure == []
    assert report.schema_version == constants.SCHEMA_VERSION


def test_xfail_counts_as_passed() -> None:
    # A test "passed" iff status in {PASSED, XFAIL}.
    report = _applied(
        {
            F2P: TestStatus.XFAIL,
            F2P_2: TestStatus.PASSED,
            P2P: TestStatus.XFAIL,
            P2P_2: TestStatus.PASSED,
        }
    )
    assert report.resolved is True
    assert F2P in report.buckets.fail_to_pass.success
    assert P2P in report.buckets.pass_to_pass.success


# --- resolved == False paths ---------------------------------------------------------------


def test_fail2pass_failed_blocks_resolution() -> None:
    report = _applied(
        {
            F2P: TestStatus.FAILED,
            F2P_2: TestStatus.PASSED,
            P2P: TestStatus.PASSED,
            P2P_2: TestStatus.PASSED,
        }
    )
    assert report.resolved is False
    assert F2P in report.buckets.fail_to_pass.failure
    assert F2P_2 in report.buckets.fail_to_pass.success


def test_pass2pass_regression_blocks_resolution() -> None:
    report = _applied(
        {
            F2P: TestStatus.PASSED,
            F2P_2: TestStatus.PASSED,
            P2P: TestStatus.FAILED,  # regression
            P2P_2: TestStatus.PASSED,
        }
    )
    assert report.resolved is False
    assert P2P in report.buckets.pass_to_pass.failure
    assert P2P_2 in report.buckets.pass_to_pass.success


def test_fail2pass_missing_counts_as_not_passed() -> None:
    # MISSING (id never appeared) must NOT count as passed -> not resolved.
    report = _applied(
        {
            F2P: TestStatus.MISSING,
            F2P_2: TestStatus.PASSED,
            P2P: TestStatus.PASSED,
            P2P_2: TestStatus.PASSED,
        }
    )
    assert report.resolved is False
    assert F2P in report.buckets.fail_to_pass.failure


def test_other_non_pass_statuses_count_as_not_passed() -> None:
    # SKIPPED/ERROR are not in {PASSED, XFAIL} -> failures.
    report = _applied(
        {
            F2P: TestStatus.ERROR,
            F2P_2: TestStatus.SKIPPED,
            P2P: TestStatus.PASSED,
            P2P_2: TestStatus.PASSED,
        }
    )
    assert report.resolved is False
    assert F2P in report.buckets.fail_to_pass.failure
    assert F2P_2 in report.buckets.fail_to_pass.failure


def test_patch_is_none_never_resolves() -> None:
    # Even if every test passed, a None patch cannot resolve.
    report = compute_report(
        {
            F2P: TestStatus.PASSED,
            F2P_2: TestStatus.PASSED,
            P2P: TestStatus.PASSED,
            P2P_2: TestStatus.PASSED,
        },
        pass2pass=[P2P, P2P_2],
        fail2pass=[F2P, F2P_2],
        patch_exists=True,
        patch_applied=True,
        patch_is_none=True,
    )
    assert report.resolved is False
    assert report.patch_is_none is True


def test_patch_not_applied_never_resolves() -> None:
    report = compute_report(
        {
            F2P: TestStatus.PASSED,
            F2P_2: TestStatus.PASSED,
            P2P: TestStatus.PASSED,
            P2P_2: TestStatus.PASSED,
        },
        pass2pass=[P2P, P2P_2],
        fail2pass=[F2P, F2P_2],
        patch_exists=False,
        patch_applied=False,
        patch_is_none=True,
    )
    assert report.resolved is False
    assert report.patch_applied is False


# --- per_test, ordering, run_id ------------------------------------------------------------


def test_per_test_maps_id_to_status_value() -> None:
    report = _applied(
        {
            F2P: TestStatus.PASSED,
            F2P_2: TestStatus.FAILED,
            P2P: TestStatus.PASSED,
            P2P_2: TestStatus.PASSED,
        }
    )
    # per_test stores the .value strings, only for bucket-relevant ids.
    assert report.per_test[F2P] == "PASSED"
    assert report.per_test[F2P_2] == "FAILED"
    assert isinstance(report.per_test[P2P], str)


def test_bucket_order_preserves_input_order() -> None:
    report = _applied(
        {
            F2P: TestStatus.PASSED,
            F2P_2: TestStatus.PASSED,
            P2P: TestStatus.PASSED,
            P2P_2: TestStatus.PASSED,
        }
    )
    assert report.buckets.fail_to_pass.success == [F2P, F2P_2]


def test_run_id_defaults_none_and_is_settable() -> None:
    report = _applied(
        {F2P: TestStatus.PASSED, F2P_2: TestStatus.PASSED, P2P: TestStatus.PASSED, P2P_2: TestStatus.PASSED}
    )
    assert report.run_id is None
    report2 = report.model_copy(update={"run_id": "run-123"})
    assert report2.run_id == "run-123"


# --- baseline transition buckets -----------------------------------------------------------


def test_baseline_none_leaves_transition_buckets_empty() -> None:
    report = _applied(
        {F2P: TestStatus.PASSED, F2P_2: TestStatus.PASSED, P2P: TestStatus.PASSED, P2P_2: TestStatus.PASSED}
    )
    # The after-state buckets are populated; the baseline-relative ones are the empty default.
    assert report.buckets.fail_to_fail == Group()
    assert report.buckets.pass_to_fail == Group()
    assert TransitionBuckets() == TransitionBuckets(
        fail_to_pass=Group(), pass_to_pass=Group(), fail_to_fail=Group(), pass_to_fail=Group()
    )


def test_baseline_populates_fail_to_fail_and_pass_to_fail() -> None:
    baseline = {
        F2P: TestStatus.FAILED,
        F2P_2: TestStatus.FAILED,
        P2P: TestStatus.PASSED,
        P2P_2: TestStatus.PASSED,
    }
    after = {
        F2P: TestStatus.PASSED,  # FAIL -> PASS
        F2P_2: TestStatus.FAILED,  # FAIL -> FAIL
        P2P: TestStatus.PASSED,  # PASS -> PASS
        P2P_2: TestStatus.FAILED,  # PASS -> FAIL (regression)
    }
    report = compute_report(
        after,
        pass2pass=[P2P, P2P_2],
        fail2pass=[F2P, F2P_2],
        patch_exists=True,
        patch_applied=True,
        patch_is_none=False,
        baseline=baseline,
    )
    assert F2P_2 in report.buckets.fail_to_fail.failure
    assert P2P_2 in report.buckets.pass_to_fail.failure
    # The conjunctive verdict still holds independently of transition diagnostics.
    assert report.resolved is False


# --- render_table --------------------------------------------------------------------------


def test_render_table_resolved_verdict_and_counts() -> None:
    report = _applied(
        {F2P: TestStatus.PASSED, F2P_2: TestStatus.PASSED, P2P: TestStatus.PASSED, P2P_2: TestStatus.PASSED}
    )
    table = render_table(report)
    assert "RESOLVED" in table
    assert "NOT RESOLVED" not in table


def test_render_table_lists_failing_ids() -> None:
    report = _applied(
        {F2P: TestStatus.FAILED, F2P_2: TestStatus.PASSED, P2P: TestStatus.PASSED, P2P_2: TestStatus.PASSED}
    )
    table = render_table(report)
    assert "NOT RESOLVED" in table
    assert F2P in table  # the failing id is surfaced


# --- write_run_json round-trip -------------------------------------------------------------


def test_write_run_json_round_trips(tmp_path: Path) -> None:
    report = _applied(
        {F2P: TestStatus.PASSED, F2P_2: TestStatus.FAILED, P2P: TestStatus.PASSED, P2P_2: TestStatus.PASSED}
    )
    out = tmp_path / "run.json"
    write_run_json(report, out)

    raw = json.loads(out.read_text(encoding="utf-8"))
    assert raw["schema_version"] == constants.SCHEMA_VERSION
    assert raw["resolved"] is False
    assert raw["buckets"]["fail_to_pass"]["failure"] == [F2P_2]

    reloaded = RunReport.model_validate(raw)
    assert reloaded == report
