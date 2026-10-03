"""End-to-end grade on the synthetic hello-bug bundle (real Docker).

Proves the load-bearing loop: golden patch -> resolved; no patch -> not resolved (fail2pass still
fails on baseline); and the solver view genuinely lacks the hidden fail2pass test.
Skipped if Docker is down or the image hasn't been built (run examples/hello-bug/build.py).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from taskbundle.bundle.loader import Bundle, load_bundle
from taskbundle.containers.client import DockerRuntime
from taskbundle.containers.image import provider_for
from taskbundle.containers.protocol import ResolvedImage
from taskbundle.errors import TaskBundleError
from taskbundle.harness.grade import grade
from taskbundle.harness.solver_view import materialize_solver_view

BUNDLE_DIR = Path(__file__).resolve().parent.parent / "examples" / "hello-bug"
FAIL2PASS = "tests/test_lowercase.py::test_lowercases"
PASS2PASS = "tests/test_strip_hidden.py::test_strips"


@pytest.fixture(scope="module")
def runtime() -> DockerRuntime:
    try:
        return DockerRuntime()
    except TaskBundleError as exc:
        pytest.skip(f"docker not available: {exc}")


@pytest.fixture(scope="module")
def bundle() -> Bundle:
    if not (BUNDLE_DIR / "task.json").exists():
        pytest.skip("run examples/hello-bug/build.py to generate the bundle first")
    return load_bundle(BUNDLE_DIR)


@pytest.fixture(scope="module")
def image(runtime: DockerRuntime, bundle: Bundle) -> ResolvedImage:
    try:
        return provider_for(bundle.spec, runtime).ensure_image()
    except TaskBundleError as exc:
        pytest.skip(f"image not built (run examples/hello-bug/build.py): {exc}")


def test_golden_patch_resolves(runtime: DockerRuntime, image: ResolvedImage, bundle: Bundle) -> None:
    report = grade(runtime, image, bundle, bundle.patch or "", patch_is_none=False)
    assert report.resolved is True
    assert report.patch_applied is True
    assert report.buckets.fail_to_pass.success == [FAIL2PASS]
    assert report.buckets.pass_to_pass.success == [PASS2PASS]


def test_noop_does_not_resolve(runtime: DockerRuntime, image: ResolvedImage, bundle: Bundle) -> None:
    report = grade(runtime, image, bundle, "", patch_is_none=True)
    assert report.resolved is False
    assert report.buckets.fail_to_pass.failure == [FAIL2PASS]  # fails on the buggy baseline
    assert report.buckets.pass_to_pass.success == [PASS2PASS]  # no regression
    assert report.per_test[FAIL2PASS] == "FAILED"


def test_gameable_check_passes_on_well_formed_task(
    runtime: DockerRuntime, image: ResolvedImage, bundle: Bundle
) -> None:
    from taskbundle.harness.validate import validate_gameable

    result = validate_gameable(runtime, image, bundle)
    assert result.holds is True  # the single fail2pass genuinely FAILS with no patch (not winnable, not missing)
    assert any("not gameable" in m for m in result.messages)


def test_solver_view_excludes_hidden_fail2pass(
    runtime: DockerRuntime, image: ResolvedImage, bundle: Bundle, tmp_path: Path
) -> None:
    view = materialize_solver_view(runtime, image, bundle, tmp_path / "view")
    assert (view / "tests" / "test_strip_visible.py").is_file()  # visible test present
    assert not (view / "tests" / "test_lowercase.py").exists()  # hidden fail2pass file absent
    blob = "\n".join(p.read_text(encoding="utf-8", errors="ignore") for p in view.rglob("*.py"))
    assert "test_lowercases" not in blob  # the hidden symbol is nowhere in the solver's tree
