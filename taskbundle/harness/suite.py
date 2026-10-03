"""`task suite`: run a matrix of (bundle x solver) with bounded concurrency, grouped by one suite_id.

A suite is a thin WRAPPER over the single-task spine: each cell is a clean, isolated `run_task`, and
the suite records them into one shared store so `task report` can aggregate. Per-cell failures are
isolated (recorded, never fatal to the suite) — the precondition the spine already guarantees.
"""

from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path

from taskbundle import constants
from taskbundle.bundle.loader import load_bundle
from taskbundle.db.store import ResultRow, RunRecord, Store
from taskbundle.harness import services
from taskbundle.harness.run import RunOutcome, run_task


@dataclass
class SuiteRunResult:
    task_id: str
    solver: str
    resolved: bool
    run_id: str | None
    error: str | None = None
    skipped: bool = False


@dataclass
class SuiteResult:
    suite_id: str
    name: str
    results: list[SuiteRunResult] = field(default_factory=list)

    @property
    def resolved_count(self) -> int:
        return sum(1 for result in self.results if result.resolved)


def run_suite(
    bundle_dirs: list[Path], solvers: list[str], *, name: str, store_dir: Path, workers: int, resume: bool = False
) -> SuiteResult:
    """Run every (bundle, solver) cell concurrently; record into a shared store under one suite_id.

    `resume=True` skips cells whose (task_id, solver) is already resolved in the store (edge #21).
    """
    store = Store(services.data_dir(store_dir) / constants.DB_FILENAME)
    suite_id = services.new_id("suite")
    store.create_suite(suite_id, name, json.dumps({"bundles": [str(b) for b in bundle_dirs], "solvers": list(solvers)}))
    skip = store.resolved_cells() if resume else set()
    jobs = [(bundle_dir, solver) for bundle_dir in bundle_dirs for solver in solvers]
    results: list[SuiteRunResult] = []
    try:
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = [pool.submit(_run_cell, bundle_dir, solver, store, suite_id, skip) for bundle_dir, solver in jobs]
            for future in as_completed(futures):
                results.append(future.result())
    finally:
        store.close()
    return SuiteResult(suite_id=suite_id, name=name, results=results)


def _run_cell(bundle_dir: Path, solver: str, store: Store, suite_id: str, skip: set[tuple[str, str]]) -> SuiteRunResult:
    try:
        bundle = load_bundle(bundle_dir)
        if (bundle.spec.id, solver) in skip:  # idempotent resume: already resolved, don't redo it.
            return SuiteRunResult(bundle.spec.id, solver, resolved=True, run_id=None, skipped=True)
        outcome = run_task(bundle_dir, solver)
        _record(store, suite_id, bundle_dir, solver, outcome)
        return SuiteRunResult(bundle.spec.id, solver, outcome.report.resolved, outcome.run_id)
    except Exception as exc:  # one bad bundle / failed run must not abort the rest of the suite.
        # Persist the failure so it's queryable (task log <cmd-id>) — not console-only.
        command_id = services.new_id("cmd")
        args: dict[str, object] = {"solver": solver, "suite_id": suite_id, "bundle_dir": str(bundle_dir)}
        store.record_command(command_id, "suite-cell", str(bundle_dir), args)
        store.finish_command(command_id, "error", str(exc))
        return SuiteRunResult(str(bundle_dir), solver, resolved=False, run_id=None, error=str(exc))


def _record(store: Store, suite_id: str, bundle_dir: Path, solver: str, outcome: RunOutcome) -> None:
    spec = load_bundle(bundle_dir).spec
    report = outcome.report
    store.upsert_task(
        spec.id,
        spec.provenance.source,
        instance_id=spec.provenance.instance_id,
        repo=spec.repo,
        language=spec.language,
        domain=spec.domain,
    )
    store.record_run(
        RunRecord(
            run_id=outcome.run_id,
            suite_id=suite_id,
            task_id=spec.id,
            solver=solver,
            base_commit=spec.base_commit,
            verdict="resolved" if report.resolved else "not-resolved",
            resolved=report.resolved,
            patch_exists=report.patch_exists,
            patch_applied=report.patch_applied,
            patch_is_none=report.patch_is_none,
        )
    )
    fail2pass = set(spec.buckets.fail2pass)
    store.record_test_results(
        outcome.run_id,
        [
            ResultRow(test_id=tid, bucket="fail2pass" if tid in fail2pass else "pass2pass", outcome=status)
            for tid, status in report.per_test.items()
        ],
    )
