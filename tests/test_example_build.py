"""The shared example image must leave repair and synthesis on the same revision."""

from __future__ import annotations

import importlib.util
import json
import subprocess
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[1]
BUNDLES = ("hello-bug", "hello-bug-synthesis")
CAPTURED_COMMIT = "1234567890abcdef1234567890abcdef12345678"


@pytest.fixture
def builder(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> ModuleType:
    spec = importlib.util.spec_from_file_location("hello_example_build", ROOT / "examples/hello-bug/build.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    examples = tmp_path / "examples"
    for name in BUNDLES:
        target = examples / name / "task.json"
        target.parent.mkdir(parents=True)
        target.write_bytes((ROOT / "examples" / name / "task.json").read_bytes())
    monkeypatch.setattr(module, "HERE", examples / "hello-bug")
    return module


def _specs(builder: ModuleType) -> dict[str, Any]:
    return {
        name: json.loads((builder.HERE.parent / name / "task.json").read_text(encoding="utf-8")) for name in BUNDLES
    }


def _docker_replies(
    builder: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    *,
    commit: str = CAPTURED_COMMIT,
    build_exit: int = 0,
    revision_exit: int = 0,
) -> list[tuple[str, ...]]:
    calls: list[tuple[str, ...]] = []

    def fake_run(*args: str) -> subprocess.CompletedProcess[str]:
        calls.append(args)
        if len(calls) == 1:
            return subprocess.CompletedProcess(args, build_exit, "", "build failed" if build_exit else "")
        assert len(calls) == 2
        return subprocess.CompletedProcess(
            args, revision_exit, commit + "\n", "revision failed" if revision_exit else ""
        )

    monkeypatch.setattr(builder, "_run", fake_run)
    return calls


def test_build_synchronizes_both_revisions_and_preserves_bundle_contracts(
    builder: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    before = _specs(builder)
    for name, task in before.items():
        task["image"]["digest"] = "sha256:" + "a" * 64
        (builder.HERE.parent / name / "task.json").write_text(json.dumps(task), encoding="utf-8")
    calls = _docker_replies(builder, monkeypatch)
    assert builder.main() == 0
    after = _specs(builder)
    for name in BUNDLES:
        expected = before[name]
        expected["base_commit"] = CAPTURED_COMMIT
        expected["image"]["ref"] = builder.IMAGE
        expected["image"].pop("digest")
        assert after[name] == expected
    assert calls == [
        ("docker", "build", "-t", builder.IMAGE, str(builder.HERE)),
        ("docker", "run", "--rm", builder.IMAGE, "git", "-C", "/app", "rev-parse", "HEAD"),
    ]


@pytest.mark.parametrize("build_exit,revision_exit", [(1, 0), (0, 1)])
def test_failed_docker_step_leaves_both_specs_unchanged(
    builder: ModuleType, monkeypatch: pytest.MonkeyPatch, build_exit: int, revision_exit: int
) -> None:
    before = _specs(builder)
    calls = _docker_replies(builder, monkeypatch, build_exit=build_exit, revision_exit=revision_exit)
    assert builder.main() == 1
    assert _specs(builder) == before
    assert len(calls) == (1 if build_exit else 2)


@pytest.mark.parametrize("commit", ["invalid", "a" * 39, "a" * 40 + "\nextra"])
def test_invalid_captured_revision_cannot_rewrite_specs(
    builder: ModuleType, monkeypatch: pytest.MonkeyPatch, commit: str
) -> None:
    before = _specs(builder)
    _docker_replies(builder, monkeypatch, commit=commit)
    assert builder.main() == 1
    assert _specs(builder) == before


@pytest.mark.parametrize("field,value", [("id", "other-bundle"), ("image", {"kind": "build"})])
def test_invalid_synthesis_contract_cannot_partially_update_repair(
    builder: ModuleType, monkeypatch: pytest.MonkeyPatch, field: str, value: Any
) -> None:
    synthesis = builder.HERE.parent / "hello-bug-synthesis" / "task.json"
    task = json.loads(synthesis.read_text(encoding="utf-8"))
    task[field] = value
    synthesis.write_text(json.dumps(task), encoding="utf-8")
    before = _specs(builder)
    _docker_replies(builder, monkeypatch)
    assert builder.main() == 1
    assert _specs(builder) == before


def test_resolved_target_outside_examples_is_rejected(
    builder: ModuleType, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    before = _specs(builder)
    target = builder.HERE.parent / "hello-bug-synthesis" / "task.json"
    original_resolve = Path.resolve

    def redirected(path: Path, *args: Any, **kwargs: Any) -> Path:
        return tmp_path / "outside.json" if path == target else original_resolve(path, *args, **kwargs)

    monkeypatch.setattr(Path, "resolve", redirected)
    _docker_replies(builder, monkeypatch)
    assert builder.main() == 1
    assert _specs(builder) == before
