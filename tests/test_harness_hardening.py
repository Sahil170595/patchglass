"""Offline unit tests for the hardening fixes: infra-empty retry (edge #3), host-mount guard
(edge #16), solver-network wiring, and artifact pruning. None of these need a live Docker daemon."""

from __future__ import annotations

from pathlib import Path
from typing import cast

import pytest

from taskbundle import constants
from taskbundle.bundle.loader import Bundle, load_bundle
from taskbundle.bundle.schema import SolverNetwork
from taskbundle.containers.client import DockerRuntime, HardenedConfig, _assert_no_host_mounts
from taskbundle.containers.protocol import ResolvedImage
from taskbundle.errors import GradeError, IsolationError
from taskbundle.harness import grade as grade_mod
from taskbundle.harness import integrity as integrity_mod
from taskbundle.harness.run import _prune_bulky_artifacts
from taskbundle.parsers.base import RunOutput
from taskbundle.solvers.command import CommandSolver
from taskbundle.solvers.factory import build_solver

BUNDLE_DIR = Path(__file__).resolve().parent.parent / "examples" / "hello-bug"


def _bundle() -> Bundle:
    if not (BUNDLE_DIR / "task.json").exists():
        pytest.skip("run examples/hello-bug/build.py to generate the bundle first")
    return load_bundle(BUNDLE_DIR)


# --- edge #3: empty runner output = infra failure, not a silent verdict ----------------------
def test_infra_test_paths_flags_test_and_conftest_edits() -> None:
    # edge #1: a solver patch touching test infra is a possible gaming attempt -> warn.
    patch = (
        "diff --git a/src/core.py b/src/core.py\n+++ b/src/core.py\n@@\n-x\n+y\n"
        "diff --git a/tests/conftest.py b/tests/conftest.py\n+++ b/tests/conftest.py\n@@\n-a\n+b\n"
        "diff --git a/pkg/test_foo.py b/pkg/test_foo.py\n+++ b/pkg/test_foo.py\n@@\n-a\n+b\n"
    )
    hits = integrity_mod.infra_test_paths(patch)
    assert "tests/conftest.py" in hits and "pkg/test_foo.py" in hits
    assert "src/core.py" not in hits  # real source edits are fine


def test_infra_test_paths_empty_for_pure_source_patch() -> None:
    assert integrity_mod.infra_test_paths("+++ b/widget/core.py\n+++ b/widget/util.py\n") == []


def test_test_script_prepends_service_setup_in_shell() -> None:
    # edge #4: setup_cmd runs in the SAME shell as the tests so a daemon it starts survives for them.
    with_setup = grade_mod._test_script("redis-server --daemonize yes", "pytest q", "START", "END")
    assert with_setup.startswith("redis-server --daemonize yes; echo ")
    assert "pytest q" in with_setup and "START" in with_setup and "END" in with_setup
    assert grade_mod._test_script(None, "pytest q", "START", "END").startswith("echo ")  # no setup -> no prefix


def test_normalize_eol_crlf_to_lf() -> None:
    # edge #6: a Windows-authored patch must become LF before it reaches `git apply` in the container.
    crlf = "diff --git a/x b/x\r\n--- a/x\r\n+++ b/x\r\n@@ -1 +1 @@\r\n-a\r\n+b\r\n"
    out = grade_mod._normalize_eol(crlf)
    assert "\r" not in out
    assert out == "diff --git a/x b/x\n--- a/x\n+++ b/x\n@@ -1 +1 @@\n-a\n+b\n"
    assert grade_mod._normalize_eol("already\nlf\n") == "already\nlf\n"  # idempotent on LF


def test_is_infra_empty_detects_only_truly_empty_output() -> None:
    assert grade_mod._is_infra_empty(RunOutput(stdout="", stderr="", artifacts={})) is True
    assert grade_mod._is_infra_empty(RunOutput(stdout="  \n ", stderr="warn", artifacts={})) is True  # only stderr
    assert grade_mod._is_infra_empty(RunOutput(stdout="collected 2 items", artifacts={})) is False
    assert grade_mod._is_infra_empty(RunOutput(stdout="", artifacts={"output.json": "{}"})) is False


def test_run_and_grade_retries_then_raises_on_persistent_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    bundle = _bundle()
    calls = {"n": 0}

    def _always_empty(runtime, image, b, patch, exists, config, policy):  # type: ignore[no-untyped-def]
        calls["n"] += 1
        return False, RunOutput(stdout="", stderr="", artifacts={}), ""

    monkeypatch.setattr(grade_mod, "_grade_attempt", _always_empty)
    with pytest.raises(GradeError, match="infrastructure failure"):
        grade_mod.run_and_grade(cast(DockerRuntime, None), cast(ResolvedImage, None), bundle, "")
    assert calls["n"] == constants.MAX_CONTAINER_RETRIES  # exhausted the retries before raising


def test_run_and_grade_recovers_on_a_later_attempt(monkeypatch: pytest.MonkeyPatch) -> None:
    bundle = _bundle()
    calls = {"n": 0}

    def _empty_then_real(runtime, image, b, patch, exists, config, policy):  # type: ignore[no-untyped-def]
        calls["n"] += 1
        if calls["n"] == 1:
            return False, RunOutput(stdout="", stderr="", artifacts={}), ""
        return False, RunOutput(stdout="collected 0 items", stderr="", artifacts={}), ""

    monkeypatch.setattr(grade_mod, "_grade_attempt", _empty_then_real)
    result = grade_mod.run_and_grade(cast(DockerRuntime, None), cast(ResolvedImage, None), bundle, "")
    assert calls["n"] == 2  # retried once, then succeeded — no raise
    assert result.patch_exists is False


# --- edge #16: never bind-mount the host / docker socket -------------------------------------
def test_host_mount_guard_rejects_docker_socket() -> None:
    with pytest.raises(IsolationError, match="docker socket"):
        _assert_no_host_mounts({"image": "x", "binds": ["/var/run/docker.sock:/var/run/docker.sock"]})
    with pytest.raises(IsolationError):
        _assert_no_host_mounts({"image": "x", "volumes": {"/": {"bind": "/host"}}})
    _assert_no_host_mounts({"image": "x", "network_mode": "none"})  # clean kwargs: no raise


# --- solver_network wiring ------------------------------------------------------------------
def test_solver_network_enum_values() -> None:
    assert SolverNetwork.NONE.value == "none"
    assert SolverNetwork.BRIDGE.value == "bridge"


def test_for_command_solver_honors_network() -> None:
    default = HardenedConfig.for_command_solver(cpus=1.0, mem_mb=512, pids=64)
    bridged = HardenedConfig.for_command_solver(cpus=1.0, mem_mb=512, pids=64, network="bridge")
    assert default.network == "none"
    assert bridged.network == "bridge"


def test_factory_threads_network_into_command_solver() -> None:
    bundle = _bundle()
    dummy_rt = cast(DockerRuntime, object())
    dummy_img = cast(ResolvedImage, object())
    default = cast(CommandSolver, build_solver("cmd:echo hi", bundle, runtime=dummy_rt, image=dummy_img))
    overridden = cast(
        CommandSolver, build_solver("cmd:echo hi", bundle, runtime=dummy_rt, image=dummy_img, network="bridge")
    )
    assert default._network == bundle.spec.solver_network.value  # defaults to the task's posture ("none")
    assert overridden._network == "bridge"  # CLI override wins


# --- run --no-keep-artifacts prunes the bulky solver-view snapshot ---------------------------
def test_prune_bulky_artifacts_drops_solver_view_keeps_run_json(tmp_path: Path) -> None:
    artifacts = tmp_path / "run_x"
    (artifacts / "solver_view" / "app").mkdir(parents=True)
    (artifacts / "solver_view" / "app" / "core.py").write_text("x = 1\n", encoding="utf-8")
    (artifacts / "run.json").write_text("{}", encoding="utf-8")
    _prune_bulky_artifacts(artifacts)
    assert not (artifacts / "solver_view").exists()  # bulky snapshot gone
    assert (artifacts / "run.json").is_file()  # eval artifact retained
