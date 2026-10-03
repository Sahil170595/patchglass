"""BenchAdapter seam: the ingestion boundary. Many benches -> one bundle (the narrow waist)."""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path
from typing import Protocol, runtime_checkable


@runtime_checkable
class BenchAdapter(Protocol):
    """Convert a benchmark's instances into bundles. Register by name; ship swebench-pro + custom first."""

    name: str

    def iter_instances(self) -> Iterable[str]:
        """Yield available instance ids for this bench."""
        ...

    def to_bundle(self, instance_id: str, out_dir: Path) -> Path:
        """Materialize one instance as a bundle directory; return its path."""
        ...
