"""services.command_log: every CLI work-command records a queryable `commands` row (offline)."""

from __future__ import annotations

from pathlib import Path

import pytest

from taskbundle.harness import services


def test_command_log_records_ok(tmp_path: Path) -> None:
    with services.command_log(tmp_path, "validate", {"patched": False}, bundle_id="task-1") as (store, cid):
        assert cid.startswith("cmd_")
    row = services.open_store(tmp_path).get_command(cid)
    assert row is not None
    assert row.type == "validate" and row.status == "ok" and row.bundle_id == "task-1"
    assert row.args == {"patched": False}
    assert row.finished_at is not None


def test_command_log_records_error_and_reraises(tmp_path: Path) -> None:
    with pytest.raises(RuntimeError, match="boom"), services.command_log(tmp_path, "run", {}) as (_store, cid):
        raise RuntimeError("boom")
    row = services.open_store(tmp_path).get_command(cid)
    assert row is not None
    assert row.status == "error" and "boom" in (row.error or "")
