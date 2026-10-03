"""Store contract — command/run/test-result round-trips, queryable by id."""

from __future__ import annotations

from pathlib import Path

from taskbundle.db.store import ResultRow, RunRecord, Store


def _store(tmp_path: Path) -> Store:
    return Store(tmp_path / "db" / "taskbundle.db")


def test_command_roundtrip(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.record_command("cmd-1", "run", "bundle-x", {"solver": "golden"})
    store.finish_command("cmd-1", "ok")
    got = store.get_command("cmd-1")
    assert got is not None
    assert got.type == "run"
    assert got.status == "ok"
    assert got.args["solver"] == "golden"
    assert got.finished_at is not None
    store.close()


def test_missing_command_is_none(tmp_path: Path) -> None:
    store = _store(tmp_path)
    assert store.get_command("nope") is None
    store.close()


def test_run_with_results(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.record_command("cmd-2", "run", "bundle-x", {})
    store.record_run(RunRecord(run_id="run-1", command_id="cmd-2", solver="golden", resolved=True, patch_applied=True))
    store.record_test_results(
        "run-1",
        [
            ResultRow(test_id="t.py::a", bucket="pass2pass", outcome="PASSED", duration_ms=12),
            ResultRow(test_id="t.py::b", bucket="fail2pass", outcome="PASSED", duration_ms=7),
        ],
    )
    run = store.get_run("run-1")
    assert run is not None
    assert run["resolved"] == 1  # bool -> 0/1
    results = run["test_results"]
    assert isinstance(results, list)
    assert len(results) == 2
    store.close()


def test_recent_commands_ordering(tmp_path: Path) -> None:
    store = _store(tmp_path)
    for i in range(3):
        store.record_command(f"c-{i}", "init", None, {})
    recent = store.recent_commands(limit=2)
    assert len(recent) == 2
    store.close()
