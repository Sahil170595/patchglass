"""`task init`: obtain the env image, record its digest (determinism anchor), log the command."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from taskbundle.bundle.loader import TASK_JSON, Bundle, load_bundle
from taskbundle.bundle.schema import is_registry_ref
from taskbundle.constants import JSON_INDENT
from taskbundle.containers.client import DockerRuntime
from taskbundle.containers.image import provider_for
from taskbundle.containers.protocol import ResolvedImage
from taskbundle.harness import services

_SHA256_PREFIX = "sha256:"


@dataclass
class InitResult:
    bundle: Bundle
    image: ResolvedImage


def init_bundle(bundle_dir: Path, *, runtime: DockerRuntime | None = None) -> InitResult:
    """Load + validate the bundle, obtain its image, and record the resolved digest in task.json + DB."""
    bundle = load_bundle(bundle_dir)
    runtime = runtime or DockerRuntime()
    with services.command_log(bundle.root, "init", {}, bundle_id=bundle.spec.id) as (store, _command_id):
        image = provider_for(bundle.spec, runtime).ensure_image()
        _persist_image_digest(bundle_dir, image)  # pin the bundle portably, not just in the DB.
        store.upsert_task(
            bundle.spec.id,
            bundle.spec.provenance.source,
            instance_id=bundle.spec.provenance.instance_id,
            repo=bundle.spec.repo,
            language=bundle.spec.language,
            domain=bundle.spec.domain,
            image_ref=image.ref,
            image_digest=image.digest,
        )
        return InitResult(bundle=bundle, image=image)


def _persist_image_digest(bundle_dir: Path, image: ResolvedImage) -> None:
    """Write the resolved registry digest back into task.json so the bundle is portably pinned.

    Only a portable REGISTRY digest is persisted (namespaced repo). A locally-built image (the synthetic
    hello-bug, or any build-path image) has only a machine-local id that would not reproduce elsewhere,
    so it is left unpinned (the validate gate then advises rather than errors).
    """
    if not image.digest.startswith(_SHA256_PREFIX):
        return
    path = bundle_dir / TASK_JSON
    raw = json.loads(path.read_text(encoding="utf-8"))
    spec_image = raw.get("image")
    if not isinstance(spec_image, dict) or spec_image.get("kind") != "prebuilt":
        return
    if not is_registry_ref(str(spec_image.get("ref", ""))):
        return  # bare local tag -> not portably pinnable; leave it for the user to rebuild.
    if spec_image.get("digest") == image.digest:
        return  # already pinned to this digest — no rewrite.
    spec_image["digest"] = image.digest
    path.write_text(json.dumps(raw, indent=JSON_INDENT) + "\n", encoding="utf-8")
