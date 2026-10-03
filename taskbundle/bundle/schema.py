"""Typed `task.json` schema — the contract every command validates against on load.

The `image` field is a discriminated union (the language-agnostic seam):
  - prebuilt: pull a pinned image (SWE-bench Pro path; fast, reproducible).
  - build:    clone repo at commit + run declared build_cmd on a digest-pinned base (arbitrary repos).
Both converge to "an env image with the repo + deps, ready to reset to base_commit".
"""

from __future__ import annotations

import re
from enum import StrEnum
from typing import Annotated, Literal

from pydantic import BaseModel, Field, model_validator

from taskbundle import constants

_SHA_RE = re.compile(r"^[0-9a-fA-F]{7,40}$")  # short or full git SHA.


class HiddenSource(StrEnum):
    """Where the hidden grading tests come from at grade time."""

    IMAGE_GIT = "image-git"  # `git checkout {instance_commit} -- <files>` (SWE-bench Pro).
    BUNDLE_FILES = "bundle-files"  # tests/{pass2pass,fail2pass}/ copied in (synthetic bundles).


class SolverNetwork(StrEnum):
    """Network posture for the command-solver container (LLM solvers run host-side, never in-container).

    `none` (default) = fully isolated. `bridge` = opt-in unfiltered egress for command solvers that must
    install tooling; it is an explicit task-author choice and weakens isolation, so it is never the default.
    """

    NONE = "none"
    BRIDGE = "bridge"


def is_registry_ref(ref: str) -> bool:
    """True if `ref`'s repository is registry-namespaced (contains '/'), e.g. 'jefzda/sweap-images'.

    A bare local tag like 'taskbundle-hello-bug:1' is NOT portably pinnable: Docker's containerd store
    hands every local image a `@sha256:` digest, but it references the local build, not a registry
    manifest, so it would not reproduce on another machine. We pin/gate only namespaced registry images.
    """
    repo = ref.split("@", 1)[0].rsplit(":", 1)[0]
    return "/" in repo


class PrebuiltImage(BaseModel):
    """Pull a pre-built per-instance image, pinned by digest for determinism."""

    kind: Literal["prebuilt"] = "prebuilt"
    ref: str = Field(description="Image reference, e.g. jefzda/sweap-images:<tag>.")
    digest: str | None = Field(default=None, description="Resolved sha256 digest; recorded at init.")
    platform: str = constants.DEFAULT_PLATFORM
    workdir: str = constants.DEFAULT_WORKDIR

    def pinned_ref(self) -> str:
        """The reference to pull by: `repo@sha256:...` when a digest is recorded, else the floating tag.

        Pulling by digest yields the EXACT image regardless of where the tag later moves — the basis of
        cross-machine determinism. A ref that already carries an `@sha256:` digest is returned as-is.
        """
        if not self.digest or "@sha256:" in self.ref:
            return self.ref
        repo = self.ref.split("@", 1)[0].rsplit(":", 1)[0]  # drop any existing tag, keep registry[:port]/repo
        return f"{repo}@{self.digest}"


class BuildImage(BaseModel):
    """Build an env image from a digest-pinned base + a declared build command."""

    kind: Literal["build"] = "build"
    base_image: str = Field(description="Base image reference (should be digest-pinned).")
    base_image_digest: str | None = Field(default=None, description="Resolved sha256 digest.")
    build_cmd: str = Field(description="Command run once in-container after clone (e.g. 'pip install -e .').")
    platform: str = constants.DEFAULT_PLATFORM
    workdir: str = constants.DEFAULT_WORKDIR


ImageSpec = Annotated[PrebuiltImage | BuildImage, Field(discriminator="kind")]


class TestSpec(BaseModel):
    """How tests are run and parsed inside the container (language-agnostic by declaration)."""

    run_cmd: str = Field(description="Command to run the selected tests; '{test_files}' is templated in.")
    parser: str = Field(description="Built-in parser name (e.g. 'pytest') or 'file:<path>'.")
    setup_cmd: str | None = Field(
        default=None,
        description="Optional command run in-shell BEFORE the tests in the grade container — start services "
        "(xvfb/redis/db) the overridden image entrypoint would have (edge #4). Daemonize long-running ones.",
    )
    selected_test_files: list[str] = Field(default_factory=list)
    collect_cmd: str | None = Field(
        default=None,
        description="test-synthesis only: enumerate a candidate test's node ids ('{test_files}' templated), "
        "e.g. 'python -m pytest --collect-only -q {test_files}'.",
    )
    coverage_cmd: str | None = Field(
        default=None,
        description="test-synthesis: run the candidate under coverage and write coverage.json (machine-readable), "
        "e.g. 'coverage run --source=. -m pytest {test_files}; coverage json -o coverage.json'. Enables the "
        "change-coverage (dC) axis: what fraction of the golden patch's changed lines the test executes.",
    )
    stage_hidden_cmd: str | None = Field(
        default=None, description="Grader-only: stages hidden tests (e.g. git checkout from instance commit)."
    )
    hidden_source: HiddenSource = HiddenSource.BUNDLE_FILES
    grade_network: SolverNetwork = Field(
        default=SolverNetwork.NONE,
        description="Network for the GRADE container only (none | bridge). Some run_scripts (npm/redis) "
        "need network; the solver container is always isolated regardless. Constrained to the enum so a "
        "task.json cannot request 'host'.",
    )


class Buckets(BaseModel):
    """The graded test buckets. fail2pass must fail on baseline; pass2pass must pass on baseline."""

    pass2pass: list[str] = Field(default_factory=list)
    fail2pass: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _no_overlap(self) -> Buckets:
        overlap = set(self.pass2pass) & set(self.fail2pass)
        if overlap:
            raise ValueError(f"buckets overlap (a test cannot be both pass2pass and fail2pass): {sorted(overlap)}")
        return self


class Limits(BaseModel):
    """Resource + time limits (named constants as defaults)."""

    cpus: float = constants.DEFAULT_CPUS
    mem_mb: int = constants.DEFAULT_MEM_MB
    pids: int = constants.DEFAULT_PIDS_LIMIT
    grade_wall_s: int = constants.DEFAULT_GRADE_WALL_S
    solver_wall_s: int = constants.DEFAULT_SOLVER_WALL_S


class Provenance(BaseModel):
    """Where this task came from (for filtering/reporting; not used in execution)."""

    source: str = "custom"  # e.g. "swebench-pro", "custom".
    instance_id: str | None = None
    issue_categories: list[str] = Field(default_factory=list)


class TaskSpec(BaseModel):
    """Validated task.json. Construct via `bundle.loader.load_bundle`."""

    model_config = {"extra": "forbid"}

    schema_version: int = constants.SCHEMA_VERSION
    id: str = Field(min_length=1, description="Stable task id.")
    repo: str = Field(description="Repository URL.")
    base_commit: str = Field(description="Exact base commit SHA (reproducibility anchor).")
    language: str = Field(default="python", description="Informational; behavior comes from test.run_cmd.")
    domain: str = Field(default="swe", description="Task domain for reporting (e.g. swe, finance, security).")
    kind: Literal["fix", "test-synthesis"] = Field(
        default="fix",
        description="'fix': solver writes a CODE patch graded by hidden tests. 'test-synthesis': solver "
        "writes a TEST patch graded fail->pass against the golden CODE patch (patch.diff is the oracle).",
    )
    image: ImageSpec
    test: TestSpec
    buckets: Buckets
    limits: Limits = Field(default_factory=Limits)
    solver_network: SolverNetwork = SolverNetwork.NONE
    provenance: Provenance = Field(default_factory=Provenance)

    @model_validator(mode="after")
    def _check(self) -> TaskSpec:
        if self.schema_version != constants.SCHEMA_VERSION:
            raise ValueError(
                f"unsupported schema_version {self.schema_version}; this CLI supports {constants.SCHEMA_VERSION}"
            )
        if not _SHA_RE.match(self.base_commit):
            raise ValueError(f"base_commit must be a git SHA (7-40 hex chars); got {self.base_commit!r}")
        return self
