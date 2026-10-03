"""Bundle loader contract — valid dir loads, missing/invalid surfaces actionable errors."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from taskbundle.bundle.loader import load_bundle
from taskbundle.errors import BundleNotFoundError, BundleValidationError
from tests.test_schema import VALID


def _write_bundle(root: Path, *, task: dict[str, object] | None = None, with_patch: bool = True) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    (root / "task.json").write_text(json.dumps(task if task is not None else VALID), encoding="utf-8")
    (root / "description.md").write_text("# Problem\nFix the widget.", encoding="utf-8")
    if with_patch:
        (root / "patch.diff").write_text("--- a/x\n+++ b/x\n", encoding="utf-8")
    return root


def test_load_valid_bundle(tmp_path: Path) -> None:
    root = _write_bundle(tmp_path / "demo")
    bundle = load_bundle(root)
    assert bundle.spec.id == "demo-1"
    assert "widget" in bundle.description.lower()
    assert bundle.patch is not None


def test_missing_dir(tmp_path: Path) -> None:
    with pytest.raises(BundleNotFoundError):
        load_bundle(tmp_path / "nope")


def test_missing_task_json(tmp_path: Path) -> None:
    root = tmp_path / "empty"
    root.mkdir()
    with pytest.raises(BundleNotFoundError, match="task.json"):
        load_bundle(root)


def test_invalid_task_json(tmp_path: Path) -> None:
    root = _write_bundle(tmp_path / "bad", task={"id": "x"})  # missing required fields
    with pytest.raises(BundleValidationError):
        load_bundle(root)


def test_patch_optional(tmp_path: Path) -> None:
    root = _write_bundle(tmp_path / "nopatch", with_patch=False)
    assert load_bundle(root).patch is None
