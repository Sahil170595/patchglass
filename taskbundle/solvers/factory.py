"""Resolve a --solver string into a Solver: golden/noop (stubs), cmd (containerized), llm (ollama/openai/hf)."""

from __future__ import annotations

from typing import TYPE_CHECKING

from taskbundle.bundle.loader import Bundle
from taskbundle.errors import SolverError
from taskbundle.solvers.base import Solver
from taskbundle.solvers.stub import GoldenSolver, NoOpSolver

if TYPE_CHECKING:
    from taskbundle.containers.client import DockerRuntime
    from taskbundle.containers.protocol import ResolvedImage


def build_solver(
    spec: str,
    bundle: Bundle,
    *,
    runtime: DockerRuntime | None = None,
    image: ResolvedImage | None = None,
    network: str | None = None,
    reasoning_effort: str | None = None,
    localize: bool = False,
    localize_budget: int | None = None,
    max_attempts: int = 1,
) -> Solver:
    """Map a solver spec to an implementation. Raises SolverError on an unknown/not-yet-wired spec.

    `network` overrides the task's `solver_network` for the command solver (CLI `--solver-network`).
    `reasoning_effort` (e.g. 'xhigh') is passed to reasoning LLMs like gpt-5.5 (CLI `--reasoning-effort`).
    `localize` switches the LLM solver to rank+select the relevant files (CLI `--localize`).
    """
    if spec == "golden":
        return GoldenSolver(bundle.patch)
    if spec == "noop":
        return NoOpSolver()
    if spec == "synth-golden":  # test-synthesis stub: emit the bundle's golden reproducing test.
        return GoldenSolver(bundle.solution_test, name="synth-golden")
    if spec == "synth-noop":  # test-synthesis negative control: no test -> not resolved.
        return NoOpSolver(name="synth-noop")
    if spec.startswith("llm-synth:"):  # LLM test-writer: emit a reproducing TEST patch.
        provider, _, model = spec[len("llm-synth:") :].partition("/")
        if not provider or not model:
            raise SolverError(
                "llm-synth spec must be 'llm-synth:<provider>/<model>', e.g. 'llm-synth:ollama/gemma2:2b'"
            )
        from taskbundle.solvers.llm_synth import SynthLLMSolver

        return SynthLLMSolver(provider, model)
    if spec.startswith("llm:"):
        provider, _, model = spec[len("llm:") :].partition("/")
        if not provider or not model:
            raise SolverError("llm solver spec must be 'llm:<provider>/<model>', e.g. 'llm:ollama/gemma3:12b'")
        from taskbundle.solvers.llm import LLMSolver

        return LLMSolver(
            provider,
            model,
            reasoning_effort=reasoning_effort,
            localize=localize,
            localize_budget=localize_budget,
            max_attempts=max_attempts,
            visible_tests=bundle.spec.test.selected_test_files,
        )
    if spec.startswith("cmd:"):
        command = spec[len("cmd:") :].strip()
        if not command:
            raise SolverError("command solver needs a command, e.g. cmd:'sed -i ...'")
        if runtime is None or image is None:
            raise SolverError("command solver requires an initialized runtime + image")
        from taskbundle.solvers.command import CommandSolver

        net = network if network is not None else bundle.spec.solver_network.value
        return CommandSolver(runtime, image, bundle.spec.base_commit, command, network=net)
    raise SolverError(
        f"unknown solver {spec!r}; use 'golden', 'noop', 'synth-golden', 'synth-noop', "
        "'llm:ollama/<model>', or \"cmd:'<command>'\""
    )
