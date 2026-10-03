"""BuildProvider unit tests — Dockerfile generation + content-addressed tag (offline, no docker)."""

from __future__ import annotations

from typing import cast

from taskbundle.bundle.schema import BuildImage
from taskbundle.containers.client import DockerRuntime
from taskbundle.containers.image import BuildProvider


def _provider(
    *, build_cmd: str = "pip install -e .", repo: str = "https://github.com/o/r", commit: str = "a" * 40
) -> BuildProvider:
    spec = BuildImage(base_image="python:3.12-slim", build_cmd=build_cmd, workdir="/app")
    return BuildProvider(cast("DockerRuntime", object()), spec, repo=repo, base_commit=commit)


def test_dockerfile_clones_at_commit_and_runs_build_cmd() -> None:
    dockerfile = _provider()._dockerfile()
    assert "FROM python:3.12-slim" in dockerfile
    assert "git clone" in dockerfile and "git checkout" in dockerfile
    assert "pip install -e ." in dockerfile
    assert "a" * 40 in dockerfile


def test_tag_is_deterministic_and_input_sensitive() -> None:
    tag = _provider()._tag()
    assert tag == _provider()._tag()  # deterministic
    assert tag.startswith("taskbundle-build:")
    assert tag != _provider(build_cmd="make")._tag()  # changes when inputs change
    assert tag != _provider()._tag(base_digest="sha256:" + "b" * 64)  # a different base digest -> rebuild


def test_base_ref_pins_by_digest_when_recorded() -> None:
    spec = BuildImage(
        base_image="python:3.12-slim", base_image_digest="sha256:" + "c" * 64, build_cmd="x", workdir="/app"
    )
    provider = BuildProvider(cast("DockerRuntime", object()), spec, repo="https://github.com/o/r", base_commit="a" * 40)
    assert provider._base_ref() == "python@sha256:" + "c" * 64  # pull base BY digest, not the floating tag
    assert _provider()._base_ref() == "python:3.12-slim"  # no recorded digest -> floating tag
