"""CLI boundary: `task validate` on a guardrail-violating bundle exits NON-ZERO (invariant #2).

Docker + the grade are monkeypatched so this exercises the cli -> _fail -> typer.Exit(1) chain offline.
"""

from __future__ import annotations

import json
import types
from pathlib import Path

import pytest
from typer.testing import CliRunner

import taskbundle.containers.client as client_mod
import taskbundle.containers.image as image_mod
import taskbundle.harness.validate as v
from taskbundle.cli import app
from taskbundle.containers.protocol import ResolvedImage
from taskbundle.reporting import Group, RunReport, TransitionBuckets

runner = CliRunner()

_TASK = {
    "id": "demo-1",
    "repo": "https://github.com/octo/widget",
    "base_commit": "a" * 40,
    "image": {"kind": "prebuilt", "ref": "x:1"},
    "test": {"run_cmd": "pytest {test_files}", "parser": "pytest", "selected_test_files": ["t.py"]},
    "buckets": {"pass2pass": ["t.py::test_a"], "fail2pass": ["t.py::test_b"]},
}


def _bundle(tmp_path: Path) -> Path:
    d = tmp_path / "b"
    d.mkdir()
    (d / "task.json").write_text(json.dumps(_TASK), encoding="utf-8")
    (d / "description.md").write_text("demo", encoding="utf-8")
    return d


def _violation_report() -> RunReport:
    # a fail2pass that PASSES on baseline -> guardrail violated
    return RunReport(
        resolved=False,
        patch_exists=False,
        patch_applied=False,
        patch_is_none=True,
        buckets=TransitionBuckets(fail_to_pass=Group(success=["t.py::test_b"])),
    )


def test_validate_exits_nonzero_on_guardrail_violation(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    img = ResolvedImage(ref="x:1", digest="", platform="linux/amd64", workdir="/app")
    monkeypatch.setattr(client_mod, "DockerRuntime", lambda: object())
    monkeypatch.setattr(image_mod, "provider_for", lambda *a, **k: types.SimpleNamespace(ensure_image=lambda: img))
    monkeypatch.setattr(
        v,
        "validate_baseline",
        lambda *a, **k: v.ValidateResult(
            holds=False, report=_violation_report(), messages=["fail2pass tests PASS on baseline (must fail)"]
        ),
    )
    result = runner.invoke(app, ["validate", "--bundle", str(_bundle(tmp_path))])
    assert result.exit_code == 1  # _fail -> typer.Exit(1), not a swallowed warning
    assert "guardrail violated" in result.output
