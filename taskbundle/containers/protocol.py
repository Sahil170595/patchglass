"""Container seams: ImageProvider (obtain an env image) and Runtime (run/exec/copy under isolation).

Implement Docker first; the Runtime protocol leaves room for Modal/K8s backends as later vertebrae.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable


@dataclass
class ExecResult:
    """Result of running a command in a container."""

    stdout: str
    stderr: str
    exit_code: int
    duration_ms: int
    oom_killed: bool = False
    timed_out: bool = False


@dataclass
class ResolvedImage:
    """An obtained env image, pinned for determinism."""

    ref: str
    digest: str
    platform: str
    workdir: str


@runtime_checkable
class ImageProvider(Protocol):
    """Obtain the environment image (pull a prebuilt one or build from a base)."""

    def ensure_image(self) -> ResolvedImage:
        """Return the resolved image (ref + sha256 digest), pulling/building as needed."""
        ...


@runtime_checkable
class Runtime(Protocol):
    """A container execution backend (Docker is the first implementation)."""

    def run(self, image: ResolvedImage, command: list[str], *, network: str, wall_clock_s: int) -> ExecResult:
        """Create a hardened container, run `command`, capture output, enforce the wall-clock limit."""
        ...

    def put_archive(self, container_id: str, dest_dir: str, tar_bytes: bytes) -> None:
        """Stream a tar into the container (used to stage hidden tests — never a bind mount)."""
        ...

    def get_archive(self, container_id: str, src_path: str) -> bytes:
        """Stream a path out of the container as a tar (used to extract the solver's diff)."""
        ...
