"""Solver seam. The boundary is workspace-handle -> patch, so an agentic solver (which uses the
optional exec capability) is a swappable vertebra, not a spine change."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Protocol, runtime_checkable

from taskbundle.containers.protocol import ExecResult


class PatchStatus(StrEnum):
    """Why a solver's patch is (or isn't) usable — the diagnostic taxonomy (AutoCodeRover-style).

    Turns "the solver produced nothing useful" into a classified, queryable signal: did the model
    emit no edits, edits that referenced code it never saw, or a real applied change?
    """

    NO_BLOCKS = "no_blocks"  # model emitted no parseable edit blocks at all.
    NO_MATCH = "no_match"  # blocks parsed, but none matched file content (hallucinated/unseen context).
    EMPTY_DIFF = "empty_diff"  # an edit matched but produced a no-op diff (search == replace).
    APPLIED = "applied"  # produced a non-empty patch.


@dataclass
class WorkspaceHandle:
    """What a solver is given: the materialized solver view (files) + an optional exec capability.

    Single-shot solvers read `root` and return a patch. An agentic solver may call `exec_fn` to run
    commands inside the sandboxed solver container. `exec_fn` is None when no live container is offered.
    """

    root: Path
    exec_fn: Callable[[list[str]], ExecResult] | None = None


@dataclass
class SolverResult:
    """A solver's output: a unified diff plus optional cost/telemetry (tokens, wall-clock, retries).

    `raw_response` is the model's full output (reasoning + proposed edits, pre-apply) and `context_files`
    is the file list it was shown — both for observability: a poor run is debuggable without re-running.
    """

    patch: str
    meta: dict[str, str | int | float] = field(default_factory=dict)
    raw_response: str | None = None
    context_files: list[str] = field(default_factory=list)


@runtime_checkable
class Solver(Protocol):
    """Produce a unified-diff patch that attempts to solve the task."""

    name: str

    def solve(self, workspace: WorkspaceHandle, description: str) -> SolverResult:
        """Given the solver view and the problem statement, return a patch."""
        ...
