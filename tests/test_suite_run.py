"""run_suite orchestration (offline): aggregation, per-cell error isolation, idempotent resume.

run_task is monkeypatched so the suite's concurrency/recording logic is exercised without Docker.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from taskbundle.harness import suite as suite_mod
from taskbundle.harness.run import RunOutcome
from taskbundle.reporting import RunReport, TransitionBuckets

BUNDLE = Path(__file__).resolve().parent.parent / "examples" / "hello-bug"


def _outcome(run_id: str, *, resolved: bool) -> RunOutcome:
    report = RunReport(
        run_id=run_id,
        resolved=resolved,
        patch_exists=resolved,
        patch_applied=resolved,
        patch_is_none=not resolved,
        buckets=TransitionBuckets(),
        per_test={"tests/t.py::a": "PASSED" if resolved else "FAILED"},
    )
    return RunOutcome(run_id=run_id, report=report, artifacts_dir=Path("."))


def _require_bundle() -> None:
    if not (BUNDLE / "task.json").exists():
        pytest.skip("examples/hello-bug bundle missing")


def test_run_suite_aggregates_and_isolates_cell_errors(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _require_bundle()

    def fake_run_task(bundle_dir: Path, solver: str, **_kw: object) -> RunOutcome:
        if solver == "boom":
            raise RuntimeError("kaboom")
        return _outcome(f"run_{solver}", resolved=(solver == "golden"))

    monkeypatch.setattr(suite_mod, "run_task", fake_run_task)
    result = suite_mod.run_suite([BUNDLE], ["golden", "noop", "boom"], name="t", store_dir=tmp_path, workers=2)
    by_solver = {r.solver: r for r in result.results}
    assert result.resolved_count == 1  # only golden resolved
    assert by_solver["golden"].resolved and not by_solver["noop"].resolved
    assert by_solver["boom"].error is not None and "kaboom" in by_solver["boom"].error  # isolated, not fatal
    assert len(result.results) == 3  # all three cells reported despite one crashing
    # the errored cell is PERSISTED (queryable), not console-only:
    from taskbundle.db.store import Store

    store = Store(tmp_path / ".taskbundle" / "taskbundle.db")
    errored = [c for c in store.recent_commands(20) if c.type == "suite-cell" and c.status == "error"]
    store.close()
    assert errored and "kaboom" in (errored[0].error or "")


def test_run_suite_resume_skips_already_resolved(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _require_bundle()
    monkeypatch.setattr(suite_mod, "run_task", lambda b, s, **k: _outcome("run_1", resolved=True))
    suite_mod.run_suite([BUNDLE], ["golden"], name="t", store_dir=tmp_path, workers=1)  # records a resolved run

    calls: list[str] = []

    def tracking_run_task(bundle_dir: Path, solver: str, **_kw: object) -> RunOutcome:
        calls.append(solver)
        return _outcome("run_2", resolved=True)

    monkeypatch.setattr(suite_mod, "run_task", tracking_run_task)
    result = suite_mod.run_suite([BUNDLE], ["golden"], name="t", store_dir=tmp_path, workers=1, resume=True)
    assert result.results[0].skipped is True  # already resolved -> skipped
    assert calls == []  # run_task was NOT called again
