"""Aggregate resolve-rate (task report) — offline test of the store query backing the batch runner."""

from __future__ import annotations

from pathlib import Path

import pytest

from taskbundle.db.store import RunRecord, Store


def _store(tmp_path: Path) -> Store:
    store = Store(tmp_path / "suite.db")
    store.create_suite("suite_1", "demo", "{}")
    store.upsert_task("t1", "synthetic", language="python", repo="octo/widget")
    store.record_run(RunRecord(run_id="r1", suite_id="suite_1", task_id="t1", solver="golden", resolved=True))
    store.record_run(RunRecord(run_id="r2", suite_id="suite_1", task_id="t1", solver="noop", resolved=False))
    return store


def test_resolve_rate_by_solver(tmp_path: Path) -> None:
    store = _store(tmp_path)
    rates = {row.group: row for row in store.resolve_rates(suite_id="suite_1", group_by="solver")}
    assert rates["golden"].resolved == 1 and rates["golden"].rate == 1.0
    assert rates["noop"].resolved == 0 and rates["noop"].rate == 0.0
    store.close()


def test_resolve_rate_by_language(tmp_path: Path) -> None:
    store = _store(tmp_path)
    rows = store.resolve_rates(suite_id="suite_1", group_by="language")
    assert len(rows) == 1
    assert rows[0].group == "python" and rows[0].total == 2 and rows[0].resolved == 1
    store.close()


def test_resolve_rate_by_domain(tmp_path: Path) -> None:
    store = Store(tmp_path / "suite.db")
    store.create_suite("s", "demo", "{}")
    store.upsert_task("fin", "synthetic", domain="finance")
    store.upsert_task("sec", "synthetic", domain="security")
    store.record_run(RunRecord(run_id="a", suite_id="s", task_id="fin", solver="llm", resolved=False))
    store.record_run(RunRecord(run_id="b", suite_id="s", task_id="sec", solver="llm", resolved=True))
    rates = {row.group: row for row in store.resolve_rates(suite_id="s", group_by="domain")}
    assert rates["finance"].resolved == 0 and rates["finance"].rate == 0.0
    assert rates["security"].resolved == 1 and rates["security"].rate == 1.0
    store.close()


def test_unknown_group_by_raises(tmp_path: Path) -> None:
    store = Store(tmp_path / "suite.db")
    with pytest.raises(ValueError, match="unknown --by"):
        store.resolve_rates(group_by="bogus")
    store.close()


def test_resolved_cells_returns_only_resolved_pairs(tmp_path: Path) -> None:
    """`suite --resume` skips (task_id, solver) cells that already have a resolved run."""
    store = _store(tmp_path)
    cells = store.resolved_cells()
    assert ("t1", "golden") in cells  # resolved=True
    assert ("t1", "noop") not in cells  # resolved=False, must be re-run
    store.close()
