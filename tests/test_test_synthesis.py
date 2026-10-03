"""Test-synthesis grade mode: compare a synthesized TEST patch before and after the
golden CODE patch. The host-side synthesizing adapter is not shown the reference patch.

A reproducing test FAILS on the buggy baseline and PASSES after the golden patch (resolved); a trivial
always-passing test never reproduces; a broken test still fails on the fixed state; a candidate that
edits non-test source is a self-fix reward-hack. The grade tests need Docker + the baked hello-bug
image; the self-fix guard runs offline.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from taskbundle.bundle.loader import Bundle, load_bundle
from taskbundle.containers.client import DockerRuntime
from taskbundle.containers.image import provider_for
from taskbundle.containers.protocol import ResolvedImage
from taskbundle.errors import TaskBundleError
from taskbundle.harness.grade import _change_coverage, changed_line_numbers, grade_test_synthesis
from taskbundle.harness.integrity import infra_test_paths, touched_files

SRC = Path(__file__).resolve().parent.parent / "examples" / "hello-bug"

_CID = ["tests/test_syn.py::test_syn"]
_REPRO = 'from widget.core import normalize\n\n\ndef test_syn():\n    assert normalize("AB") == "ab"\n'
_TRIVIAL = "def test_syn():\n    assert True\n"
_BROKEN = "def test_syn():\n    assert False\n"


def _new_test_patch(path: str, body: str) -> str:
    """A unified diff that ADDS a new test file `path` with `body`."""
    lines = body.splitlines()
    hunk = "\n".join("+" + ln for ln in lines)
    return (
        f"diff --git a/{path} b/{path}\n"
        "new file mode 100644\n--- /dev/null\n"
        f"+++ b/{path}\n@@ -0,0 +1,{len(lines)} @@\n{hunk}\n"
    )


def _golden() -> str:
    return (SRC / "patch.diff").read_text(encoding="utf-8")


# --------------------------------------------------------------------------- self-fix guard (offline)
def test_self_fix_source_edit_detected() -> None:
    # The candidate "fixes" the bug inside its own patch (edits widget/core.py) to satisfy fail->pass.
    patch = _new_test_patch("tests/test_syn.py", _REPRO) + (
        "diff --git a/widget/core.py b/widget/core.py\n--- a/widget/core.py\n+++ b/widget/core.py\n"
        "@@ -1,1 +1,1 @@\n-x\n+y\n"
    )
    test_ish = set(infra_test_paths(patch))
    source_edits = [f for f in touched_files(patch) if f not in test_ish]
    assert source_edits == ["widget/core.py"]


# --------------------------------------------------------------------------- change-coverage (offline)
def test_changed_line_numbers_multi_hunk() -> None:
    patch = (
        "diff --git a/m.py b/m.py\n--- a/m.py\n+++ b/m.py\n"
        "@@ -1,2 +1,3 @@\n a\n+b\n c\n"  # adds new-file line 2
        "@@ -10,1 +11,2 @@\n x\n+y\n"  # adds new-file line 12
    )
    assert changed_line_numbers(patch) == {"m.py": {2, 12}}


def test_change_coverage_full_partial_and_wrong_reason() -> None:
    one = "+++ b/m.py\n@@ -1,1 +1,2 @@\n a\n+b\n"  # golden changes line 2
    assert _change_coverage(one, {"m.py": {1, 2}}) == 1.0  # test executes the changed line
    assert _change_coverage(one, {"m.py": {1}}) == 0.0  # misses it -> reproduces for the wrong reason
    two = "+++ b/m.py\n@@ -1,1 +1,3 @@\n a\n+b\n+c\n"  # golden changes lines 2 and 3
    assert _change_coverage(two, {"m.py": {2}}) == 0.5  # covers one of two changed lines (discriminating)


# --------------------------------------------------------------------------- the grade (real docker)
@pytest.fixture(scope="module")
def _env() -> tuple[DockerRuntime, ResolvedImage, Bundle]:
    if not (SRC / "task.json").exists():
        pytest.skip("run examples/hello-bug/build.py first")
    try:
        runtime = DockerRuntime()
    except TaskBundleError as exc:
        pytest.skip(f"docker not available: {exc}")
    bundle = load_bundle(SRC)
    image = provider_for(bundle.spec, runtime).ensure_image()
    return runtime, image, bundle


def test_reproducing_test_resolves(_env: tuple[DockerRuntime, ResolvedImage, Bundle]) -> None:
    runtime, image, bundle = _env
    report = grade_test_synthesis(runtime, image, bundle, _new_test_patch("tests/test_syn.py", _REPRO), _golden(), _CID)
    assert report.resolved is True
    assert report.fail_to_pass == _CID
    assert report.pre["tests/test_syn.py::test_syn"] == "FAILED"  # fails on the buggy baseline
    assert report.post["tests/test_syn.py::test_syn"] == "PASSED"  # passes after the golden patch


def test_trivial_passing_test_not_resolved(_env: tuple[DockerRuntime, ResolvedImage, Bundle]) -> None:
    runtime, image, bundle = _env
    report = grade_test_synthesis(
        runtime, image, bundle, _new_test_patch("tests/test_syn.py", _TRIVIAL), _golden(), _CID
    )
    assert report.resolved is False  # passes on baseline too -> no fail->pass -> does not reproduce
    assert report.fail_to_pass == []


def test_broken_test_not_resolved(_env: tuple[DockerRuntime, ResolvedImage, Bundle]) -> None:
    runtime, image, bundle = _env
    report = grade_test_synthesis(
        runtime, image, bundle, _new_test_patch("tests/test_syn.py", _BROKEN), _golden(), _CID
    )
    assert report.resolved is False  # still fails after the golden patch -> broken test, not a repro
    assert report.post["tests/test_syn.py::test_syn"] == "FAILED"  # does not pass even on the fixed state
