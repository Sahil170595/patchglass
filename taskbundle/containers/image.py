"""ImageProvider implementations. `prebuilt` pulls a pinned image (SWE-bench Pro path); `build`
clones the repo at the commit + runs the declared build_cmd on a digest-pinned base (any repo).
Both resolve to a digest-pinned ResolvedImage so determinism is a recorded property.
"""

from __future__ import annotations

import hashlib
import shlex

from taskbundle.bundle.schema import BuildImage, PrebuiltImage, TaskSpec
from taskbundle.containers.client import DockerRuntime
from taskbundle.containers.protocol import ImageProvider, ResolvedImage


class PrebuiltProvider:
    """Pull a pre-built per-instance image and record its sha256 digest."""

    def __init__(self, runtime: DockerRuntime, spec: PrebuiltImage) -> None:
        self._runtime = runtime
        self._spec = spec

    def ensure_image(self) -> ResolvedImage:
        # If the bundle records a digest, resolve BY it (repo@sha256:...) so we get the EXACT image,
        # not whatever the mutable floating tag currently points to. This is what makes the verdict
        # reproducible across machines; `task init` records the digest into task.json.
        ref = self._spec.pinned_ref()
        pinned, digest = self._runtime.resolve_image(ref, self._spec.platform)
        return ResolvedImage(ref=pinned, digest=digest, platform=self._spec.platform, workdir=self._spec.workdir)


class BuildProvider:
    """Build an env image: clone repo@commit on a declared base, run build_cmd. Content-addressed tag."""

    def __init__(self, runtime: DockerRuntime, spec: BuildImage, *, repo: str, base_commit: str) -> None:
        self._runtime = runtime
        self._spec = spec
        self._repo = repo
        self._base_commit = base_commit

    def ensure_image(self) -> ResolvedImage:
        # Pin the base by DIGEST before building: resolve it (pulling by a recorded base_image_digest when
        # present, else the floating tag) so FROM is reproducible and a moved base tag forces a rebuild.
        base_pinned, base_digest = self._runtime.resolve_image(self._base_ref(), self._spec.platform)
        tag = self._tag(base_digest)
        if not self._runtime.image_present(tag):  # rebuild only when inputs (incl. base digest) change.
            self._runtime.build_image(self._dockerfile(base_pinned), tag, self._spec.platform)
        ref, digest = self._runtime.resolve_image(tag, self._spec.platform)
        return ResolvedImage(ref=ref, digest=digest, platform=self._spec.platform, workdir=self._spec.workdir)

    def _base_ref(self) -> str:
        """Base image to build FROM: pinned by digest (repo@sha256) when recorded, else the floating tag."""
        if self._spec.base_image_digest:
            repo = self._spec.base_image.split("@", 1)[0].rsplit(":", 1)[0]
            return f"{repo}@{self._spec.base_image_digest}"
        return self._spec.base_image

    def _tag(self, base_digest: str = "") -> str:
        # Content-address by the RESOLVED base digest (not the mutable tag) so the build is reproducible.
        key = "|".join([base_digest or self._spec.base_image, self._repo, self._base_commit, self._spec.build_cmd])
        return f"taskbundle-build:{hashlib.sha256(key.encode('utf-8')).hexdigest()[:16]}"

    def _dockerfile(self, base_ref: str = "") -> str:
        repo = shlex.quote(self._repo)
        commit = shlex.quote(self._base_commit)
        return (
            f"FROM {base_ref or self._spec.base_image}\n"
            "RUN sh -c 'command -v git >/dev/null 2>&1 "
            "|| (apt-get update && apt-get install -y --no-install-recommends git) "
            "|| apk add --no-cache git'\n"
            f"WORKDIR {self._spec.workdir}\n"
            f"RUN git clone {repo} . && git checkout {commit}\n"
            f"RUN {self._spec.build_cmd}\n"
        )


def provider_for(spec: TaskSpec, runtime: DockerRuntime) -> ImageProvider:
    """Select the ImageProvider for a task's declared image source."""
    if isinstance(spec.image, PrebuiltImage):
        return PrebuiltProvider(runtime, spec.image)
    return BuildProvider(runtime, spec.image, repo=spec.repo, base_commit=spec.base_commit)
