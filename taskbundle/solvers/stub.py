"""Deterministic stub solvers — the harness self-test.

GoldenSolver returns the bundle's golden patch (a well-formed task should grade as resolved);
NoOpSolver returns nothing (the baseline should grade as not-resolved).
"""

from __future__ import annotations

from taskbundle.solvers.base import SolverResult, WorkspaceHandle


class GoldenSolver:
    """Apply a stored golden patch. Expected verdict on a well-formed task: resolved.

    Reused for test-synthesis (`name="synth-golden"`, `patch=` the golden reproducing test) — a stub
    that emits the known-good test the same way `golden` emits the known-good code fix.
    """

    def __init__(self, patch: str | None, name: str = "golden") -> None:
        self._patch = patch or ""
        self.name = name

    def solve(self, workspace: WorkspaceHandle, description: str) -> SolverResult:
        return SolverResult(patch=self._patch, meta={"solver": self.name})


class NoOpSolver:
    """Produce no patch. Expected verdict: not resolved (fail2pass still fail / no test synthesized)."""

    def __init__(self, name: str = "noop") -> None:
        self.name = name

    def solve(self, workspace: WorkspaceHandle, description: str) -> SolverResult:
        return SolverResult(patch="", meta={"solver": self.name})
