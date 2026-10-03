"""Full-lifecycle CLI e2e on the synthetic hello-bug bundle (real Docker via the typer app).

init -> validate (baseline + --patched) -> run golden (resolved) -> log -> run noop (not resolved).
Runs against a throwaway copy of the bundle so the DB starts clean. Skipped if Docker/image absent.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest
from typer.testing import CliRunner

from taskbundle.cli import app
from taskbundle.containers.client import DockerRuntime
from taskbundle.errors import TaskBundleError

SRC = Path(__file__).resolve().parent.parent / "examples" / "hello-bug"
runner = CliRunner()


@pytest.fixture(scope="module")
def _docker() -> None:
    try:
        DockerRuntime()
    except TaskBundleError as exc:
        pytest.skip(f"docker not available: {exc}")


@pytest.fixture
def bundle(tmp_path: Path, _docker: None) -> Path:
    if not (SRC / "task.json").exists():
        pytest.skip("run examples/hello-bug/build.py first")
    dst = tmp_path / "hello-bug"
    shutil.copytree(SRC, dst)
    return dst


def _run_id(output: str) -> str:
    return next(line.split("run_id:")[1].strip() for line in output.splitlines() if "run_id:" in line)


def test_full_lifecycle(bundle: Path) -> None:
    b = str(bundle)

    init = runner.invoke(app, ["init", "--bundle", b])
    assert init.exit_code == 0, init.output
    assert "image:" in init.output and "digest:" in init.output

    baseline = runner.invoke(app, ["validate", "--bundle", b])
    assert baseline.exit_code == 0, baseline.output
    assert "guardrail holds" in baseline.output

    patched = runner.invoke(app, ["validate", "--bundle", b, "--patched"])
    assert patched.exit_code == 0, patched.output

    golden = runner.invoke(app, ["run", "--bundle", b, "--solver", "golden"])
    assert golden.exit_code == 0, golden.output
    assert "verdict: RESOLVED" in golden.output
    run_id = _run_id(golden.output)

    queried = runner.invoke(app, ["log", run_id, "--bundle", b])
    assert queried.exit_code == 0, queried.output
    assert "RESOLVED" in queried.output
    assert "tests/test_lowercase.py::test_lowercases" in queried.output

    noop = runner.invoke(app, ["run", "--bundle", b, "--solver", "noop"])
    assert noop.exit_code == 0, noop.output
    assert "NOT RESOLVED" in noop.output

    listing = runner.invoke(app, ["ls", "--bundle", b])
    assert listing.exit_code == 0, listing.output
    assert "run" in listing.output and "init" in listing.output


def test_doctor_runs(_docker: None) -> None:
    result = runner.invoke(app, ["doctor"])
    assert result.exit_code == 0, result.output
    assert "docker daemon reachable" in result.output
