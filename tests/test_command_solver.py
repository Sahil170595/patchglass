"""Command solver e2e (real Docker): an isolated shell command that fixes hello-bug resolves it."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from taskbundle.containers.client import DockerRuntime
from taskbundle.errors import TaskBundleError
from taskbundle.harness.run import run_task

SRC = Path(__file__).resolve().parent.parent / "examples" / "hello-bug"


@pytest.fixture(scope="module")
def _docker() -> None:
    try:
        DockerRuntime()
    except TaskBundleError as exc:
        pytest.skip(f"docker not available: {exc}")


def _patch(bundle: Path, run_id: str) -> str:
    return (bundle / ".taskbundle" / "runs" / run_id / "solver.patch").read_text(encoding="utf-8")


def test_command_solver_resolves(tmp_path: Path, _docker: None) -> None:
    if not (SRC / "task.json").exists():
        pytest.skip("run examples/hello-bug/build.py first")
    bundle = tmp_path / "hello-bug"
    shutil.copytree(SRC, bundle)
    spec = "cmd:sed -i 's/text.strip()/text.strip().lower()/' widget/core.py"
    outcome = run_task(bundle, spec)
    assert outcome.report.resolved is True
    assert "lower()" in _patch(bundle, outcome.run_id or "")


def test_command_solver_runs_as_non_root(tmp_path: Path, _docker: None) -> None:
    # Least privilege: the untrusted command executes as a non-root uid (not 0), even though the
    # trusted harness reset/chmod the root-owned repo as root. Proven by recording id into the diff.
    if not (SRC / "task.json").exists():
        pytest.skip("run examples/hello-bug/build.py first")
    bundle = tmp_path / "hello-bug"
    shutil.copytree(SRC, bundle)
    outcome = run_task(bundle, 'cmd:sed -i "1i# uid=$(id -u)" widget/core.py')
    patch = _patch(bundle, outcome.run_id or "")
    assert "# uid=65534" in patch  # ran as nobody, not root
    assert "# uid=0" not in patch
