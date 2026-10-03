"""End-to-end `task run` on a test-synthesis bundle.

`run_task` dispatches on `spec.kind`: for 'test-synthesis' it runs a test-writer solver, discovers the
candidate's node ids via a collect-only pass, and grades them fail->pass against the golden code patch
the harness holds. Needs Docker + the baked hello-bug image (the synthesis bundle reuses it).
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from taskbundle.bundle.loader import Bundle, load_bundle
from taskbundle.containers.client import DockerRuntime
from taskbundle.containers.image import provider_for
from taskbundle.containers.protocol import ResolvedImage
from taskbundle.errors import TaskBundleError
from taskbundle.harness.run import run_task
from taskbundle.harness.validate import validate_synthesis

SRC = Path(__file__).resolve().parent.parent / "examples" / "hello-bug-synthesis"
_CANDIDATE = "tests/test_syn.py::test_syn"
_TRIVIAL_SOLUTION = (
    "diff --git a/tests/test_syn.py b/tests/test_syn.py\n"
    "new file mode 100644\n--- /dev/null\n+++ b/tests/test_syn.py\n"
    "@@ -0,0 +1,2 @@\n+def test_syn():\n+    assert True\n"
)


@pytest.fixture(scope="module")
def _docker() -> None:
    if not (SRC / "task.json").exists() or not (SRC.parent / "hello-bug" / "task.json").exists():
        pytest.skip("run examples/hello-bug/build.py first (the synthesis bundle reuses its image)")
    try:
        DockerRuntime()
    except TaskBundleError as exc:
        pytest.skip(f"docker not available: {exc}")


@pytest.fixture(scope="module")
def _env(_docker: None) -> tuple[DockerRuntime, ResolvedImage, Bundle]:
    runtime = DockerRuntime()
    bundle = load_bundle(SRC)
    image = provider_for(bundle.spec, runtime).ensure_image()
    return runtime, image, bundle


def test_synth_golden_resolves(tmp_path: Path, _docker: None) -> None:
    bundle = tmp_path / "syn"
    shutil.copytree(SRC, bundle)
    outcome = run_task(bundle, "synth-golden")
    assert outcome.report.resolved is True  # the reproducing test is fail->pass vs the golden patch
    assert outcome.report.buckets.fail_to_pass.success == [_CANDIDATE]
    assert any("dC=1.00" in w for w in outcome.report.warnings)  # covers the golden patch's changed line


def test_synth_noop_not_resolved(tmp_path: Path, _docker: None) -> None:
    bundle = tmp_path / "syn"
    shutil.copytree(SRC, bundle)
    outcome = run_task(bundle, "synth-noop")
    assert outcome.report.resolved is False  # no test synthesized -> nothing reproduces
    assert outcome.report.buckets.fail_to_pass.success == []


def test_validate_synthesis_holds(_env: tuple[DockerRuntime, ResolvedImage, Bundle]) -> None:
    runtime, image, bundle = _env
    result = validate_synthesis(runtime, image, bundle)
    assert result.holds is True  # the bundle's golden reproducing test IS fail->pass


def test_validate_synthesis_rejects_non_reproducing(
    tmp_path: Path, _env: tuple[DockerRuntime, ResolvedImage, Bundle]
) -> None:
    runtime, image, _ = _env
    broken = tmp_path / "broken"
    shutil.copytree(SRC, broken)
    (broken / "solution_test.diff").write_text(_TRIVIAL_SOLUTION, encoding="utf-8")  # always-passing -> no fail->pass
    result = validate_synthesis(runtime, image, load_bundle(broken))
    assert result.holds is False  # a golden test that doesn't reproduce -> malformed synthesis bundle
