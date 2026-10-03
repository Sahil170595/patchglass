"""Digest pinning (offline): pull-by-digest ref, task.json write-back, and run.json self-id.

The determinism anchor: a prebuilt bundle must carry the resolved sha256 so it pulls the EXACT image
on any machine. init records it; the provider resolves by it; validate gates a registry image that
left it unpinned. A locally-built image (no registry digest) is exempt.
"""

from __future__ import annotations

import json
from pathlib import Path

from taskbundle.bundle.schema import PrebuiltImage
from taskbundle.containers.protocol import ResolvedImage
from taskbundle.harness.init import _persist_image_digest
from taskbundle.reporting import RunReport, TransitionBuckets

_DIGEST = "sha256:" + "a" * 64


def test_pinned_ref_uses_digest_when_present() -> None:
    floating = PrebuiltImage(ref="jefzda/sweap-images:some-tag")
    assert floating.pinned_ref() == "jefzda/sweap-images:some-tag"  # no digest -> floating tag
    pinned = PrebuiltImage(ref="jefzda/sweap-images:some-tag", digest=_DIGEST)
    assert pinned.pinned_ref() == f"jefzda/sweap-images@{_DIGEST}"  # drops the tag, pins by digest
    already = PrebuiltImage(ref=f"repo@{_DIGEST}")
    assert already.pinned_ref() == f"repo@{_DIGEST}"  # already digest-pinned -> unchanged


def _write_task_json(root: Path, image: dict[str, object]) -> Path:
    path = root / "task.json"
    path.write_text(json.dumps({"id": "t", "image": image}, indent=2), encoding="utf-8")
    return path


def test_persist_writes_registry_digest_to_task_json(tmp_path: Path) -> None:
    path = _write_task_json(tmp_path, {"kind": "prebuilt", "ref": "jefzda/sweap-images:tag", "digest": None})
    resolved = ResolvedImage(
        ref=f"jefzda/sweap-images@{_DIGEST}", digest=_DIGEST, platform="linux/amd64", workdir="/app"
    )
    _persist_image_digest(tmp_path, resolved)
    assert json.loads(path.read_text(encoding="utf-8"))["image"]["digest"] == _DIGEST


def test_persist_skips_local_image_without_registry_digest(tmp_path: Path) -> None:
    # hello-bug case: a locally-built image has only a machine-local id (resolved ref has no @sha256:).
    path = _write_task_json(tmp_path, {"kind": "prebuilt", "ref": "taskbundle-hello-bug:1", "digest": None})
    resolved = ResolvedImage(ref="taskbundle-hello-bug:1", digest=_DIGEST, platform="linux/amd64", workdir="/app")
    _persist_image_digest(tmp_path, resolved)
    assert json.loads(path.read_text(encoding="utf-8"))["image"]["digest"] is None  # left unpinned


def test_run_report_records_solver_and_digest() -> None:
    report = RunReport(
        solver="llm:openai/gpt-4o",
        image_digest=_DIGEST,
        resolved=True,
        patch_exists=True,
        patch_applied=True,
        patch_is_none=False,
        buckets=TransitionBuckets(),
    )
    blob = json.loads(report.model_dump_json())
    assert blob["solver"] == "llm:openai/gpt-4o"  # artifact self-identifies the producing solver
    assert blob["image_digest"] == _DIGEST
