"""`task new`: scaffold a build-path custom bundle and prove it round-trip validates (offline)."""

from __future__ import annotations

from pathlib import Path

import pytest

from taskbundle.bundle.loader import load_bundle
from taskbundle.bundle.scaffold import new_bundle
from taskbundle.bundle.schema import BuildImage, HiddenSource
from taskbundle.errors import BundleValidationError

_COMMIT = "a8f781e9a0d5b6876fa4e737cd184bd5535d4e0e"


def test_new_bundle_scaffolds_validatable_build_bundle(tmp_path: Path) -> None:
    out = new_bundle(
        tmp_path / "mytask",
        task_id="my-task",
        repo="https://github.com/octo/widget",
        base_commit=_COMMIT,
        base_image="python:3.12-slim",
        build_cmd="pip install -e .",
    )
    bundle = load_bundle(out)
    assert bundle.spec.id == "my-task"
    assert isinstance(bundle.spec.image, BuildImage)
    assert bundle.spec.image.build_cmd == "pip install -e ."
    assert bundle.spec.test.hidden_source == HiddenSource.BUNDLE_FILES
    # Empty hidden-test buckets the author fills in; the dirs exist so the layout is obvious.
    assert (out / "tests" / "fail2pass").is_dir()
    assert (out / "tests" / "pass2pass").is_dir()
    assert (out / "description.md").is_file()
    assert (out / "patch.diff").is_file()


def test_new_bundle_python_defaults_to_pytest(tmp_path: Path) -> None:
    out = new_bundle(
        tmp_path / "t",
        task_id="t",
        repo="r",
        base_commit=_COMMIT,
        base_image="python:3.12-slim",
        build_cmd="pip install -e .",
    )
    spec = load_bundle(out).spec
    assert spec.test.parser == "pytest"
    assert "pytest" in spec.test.run_cmd


def test_new_bundle_unknown_language_requires_explicit_runner(tmp_path: Path) -> None:
    with pytest.raises(BundleValidationError, match=r"language 'go'"):
        new_bundle(
            tmp_path / "t",
            task_id="t",
            repo="r",
            base_commit=_COMMIT,
            base_image="golang:1.22",
            build_cmd="go build ./...",
            language="go",
        )


def test_new_bundle_explicit_runner_overrides_default(tmp_path: Path) -> None:
    out = new_bundle(
        tmp_path / "t",
        task_id="t",
        repo="r",
        base_commit=_COMMIT,
        base_image="golang:1.22",
        build_cmd="go build ./...",
        language="go",
        test_cmd="go test {test_files}",
        parser="scale_run_script",
    )
    spec = load_bundle(out).spec
    assert spec.test.run_cmd == "go test {test_files}"
    assert spec.test.parser == "scale_run_script"


def test_new_bundle_refuses_nonempty_directory(tmp_path: Path) -> None:
    dest = tmp_path / "exists"
    dest.mkdir()
    (dest / "task.json").write_text("{}", encoding="utf-8")
    with pytest.raises(BundleValidationError, match="non-empty"):
        new_bundle(
            dest,
            task_id="t",
            repo="r",
            base_commit=_COMMIT,
            base_image="python:3.12-slim",
            build_cmd="pip install -e .",
        )
