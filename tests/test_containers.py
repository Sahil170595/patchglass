"""Integration tests for the hardened Docker runtime — exercised against a REAL daemon.

Skipped automatically if Docker is unreachable. Uses a tiny public image (alpine) so the one-time
pull is fast. Proves the isolation guarantees actually hold (not just that flags were passed).
"""

from __future__ import annotations

import pytest

from taskbundle.bundle.schema import PrebuiltImage
from taskbundle.constants import DEFAULT_CPUS, DEFAULT_MEM_MB, DEFAULT_PIDS_LIMIT
from taskbundle.containers import tario
from taskbundle.containers.client import DockerRuntime, HardenedConfig
from taskbundle.containers.image import PrebuiltProvider
from taskbundle.containers.protocol import ResolvedImage
from taskbundle.errors import TaskBundleError

ALPINE = "alpine:3.20"


@pytest.fixture(scope="module")
def runtime() -> DockerRuntime:
    try:
        return DockerRuntime()
    except TaskBundleError as exc:
        pytest.skip(f"docker not available: {exc}")


@pytest.fixture(scope="module")
def image(runtime: DockerRuntime) -> ResolvedImage:
    return PrebuiltProvider(runtime, PrebuiltImage(ref=ALPINE, workdir="/")).ensure_image()


def _grading() -> HardenedConfig:
    return HardenedConfig.for_grading(cpus=DEFAULT_CPUS, mem_mb=DEFAULT_MEM_MB, pids=DEFAULT_PIDS_LIMIT)


def test_pull_resolves_digest(image: ResolvedImage) -> None:
    assert image.digest.startswith("sha256:")
    assert "alpine" in image.ref


def test_exec_captures_output(runtime: DockerRuntime, image: ResolvedImage) -> None:
    with runtime.container(image, _grading()) as box:
        result = box.exec(["echo", "hello-world"])
    assert result.exit_code == 0
    assert "hello-world" in result.stdout


def test_exec_nonzero_exit_and_stderr(runtime: DockerRuntime, image: ResolvedImage) -> None:
    with runtime.container(image, _grading()) as box:
        result = box.exec(["sh", "-c", "echo oops 1>&2; exit 3"])
    assert result.exit_code == 3
    assert "oops" in result.stderr


def test_network_none_isolates(runtime: DockerRuntime, image: ResolvedImage) -> None:
    # Under --network none only the loopback interface exists.
    with runtime.container(image, _grading()) as box:
        result = box.exec(["ls", "/sys/class/net"])
    interfaces = result.stdout.split()
    assert "lo" in interfaces
    assert "eth0" not in interfaces


def test_put_get_archive_roundtrip(runtime: DockerRuntime, image: ResolvedImage) -> None:
    with runtime.container(image, _grading()) as box:
        box.put_archive("/tmp", tario.file_to_tar("hello.txt", b"payload-123"))
        got = tario.read_member(box.get_archive("/tmp/hello.txt"), "hello.txt")
    assert got == "payload-123"


def test_wall_clock_timeout_kills(runtime: DockerRuntime, image: ResolvedImage) -> None:
    with runtime.container(image, _grading()) as box:
        result = box.exec(["sleep", "30"], timeout_s=1)
    assert result.timed_out is True
    assert result.exit_code == 124


def test_hardened_container_drops_all_capabilities(runtime: DockerRuntime, image: ResolvedImage) -> None:
    # Behavioral proof that cap_drop ALL is actually applied (not just passed as a kwarg): a regression
    # that stopped dropping caps would show a non-zero CapEff bitmask here.
    config = HardenedConfig.for_grading(cpus=DEFAULT_CPUS, mem_mb=DEFAULT_MEM_MB, pids=DEFAULT_PIDS_LIMIT)
    with runtime.container(image, config) as box:
        caps = box.exec(["sh", "-c", "grep CapEff /proc/self/status"])
    assert caps.exit_code == 0
    assert caps.stdout.split()[-1].strip("0") == ""  # CapEff all zeros -> no effective capabilities
