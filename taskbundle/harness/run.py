"""`task run`: the baseline-vs-solver flow.

Materialize the solver view (proving invariant #1) -> invoke the solver -> grade its patch in a fresh
container -> write run.json + the produced patch + DB rows. The solver never touches the grading container.
"""

from __future__ import annotations

import hashlib
import json
import shutil
from dataclasses import dataclass
from pathlib import Path

from taskbundle import __version__
from taskbundle.bundle.loader import Bundle, load_bundle
from taskbundle.containers.client import DockerRuntime
from taskbundle.containers.image import provider_for
from taskbundle.containers.protocol import ResolvedImage
from taskbundle.db.store import ResultRow, RunRecord, Store
from taskbundle.harness import services
from taskbundle.harness.grade import grade, grade_synthesis, run_and_grade
from taskbundle.harness.integrity import (
    RewardHackPolicy,
    infra_test_paths,
    scan_outcome_override,
    scan_skip_injection,
)
from taskbundle.harness.solver_view import materialize_solver_view
from taskbundle.reporting import RunReport, TransitionBuckets, write_run_json
from taskbundle.solvers.base import WorkspaceHandle
from taskbundle.solvers.factory import build_solver


@dataclass
class RunOutcome:
    run_id: str
    report: RunReport
    artifacts_dir: Path


def run_task(
    bundle_dir: Path,
    solver_spec: str,
    *,
    runtime: DockerRuntime | None = None,
    with_baseline: bool = False,
    solver_network: str | None = None,
    keep_artifacts: bool = True,
    reasoning_effort: str | None = None,
    localize: bool = False,
    localize_budget: int | None = None,
    max_attempts: int = 1,
    skip_empty_grade: bool = False,
    reward_hack_policy: RewardHackPolicy = RewardHackPolicy.BLOCK,
) -> RunOutcome:
    """Run `solver_spec` against the bundle and return the structured outcome (also persisted to the DB).

    `with_baseline` grades the unpatched baseline first so the report's FAIL_TO_FAIL / PASS_TO_FAIL
    transition buckets are populated (costs a second grading container). `solver_network` overrides the
    task's command-solver network posture. `keep_artifacts=False` prunes the bulky solver-view snapshot
    after grading. `reasoning_effort` (e.g. 'xhigh') is forwarded to reasoning LLM solvers like gpt-5.5.
    `localize` switches the LLM solver to retrieval-based file selection instead of a flat dump.
    """
    bundle = load_bundle(bundle_dir)
    runtime = runtime or DockerRuntime()
    run_id = services.new_id("run")
    artifacts = services.run_artifacts_dir(bundle.root, run_id)
    args: dict[str, object] = {"solver": solver_spec, "run_id": run_id}
    with services.command_log(bundle.root, "run", args, bundle_id=bundle.spec.id) as (store, command_id):
        image = provider_for(bundle.spec, runtime).ensure_image()
        view_root = materialize_solver_view(runtime, image, bundle, artifacts / "solver_view")
        solver = build_solver(
            solver_spec,
            bundle,
            runtime=runtime,
            image=image,
            network=solver_network,
            reasoning_effort=reasoning_effort,
            localize=localize,
            localize_budget=localize_budget,
            max_attempts=max_attempts,
        )
        result = solver.solve(WorkspaceHandle(root=view_root), bundle.description)
        (artifacts / "solver.patch").write_text(result.patch, encoding="utf-8")
        if result.raw_response is not None:  # the model's reasoning + proposed edits (observability).
            (artifacts / "solver_response.md").write_text(result.raw_response, encoding="utf-8")

        patch_is_none = not result.patch.strip()
        if bundle.spec.kind == "test-synthesis":
            # The solver wrote a TEST patch; grade it fail->pass against the golden code patch we hold.
            report = grade_synthesis(runtime, image, bundle, result.patch, artifacts_dir=artifacts)
        elif skip_empty_grade and patch_is_none:
            report = _empty_patch_report()  # perf: an empty patch can't resolve -> skip the grade container.
        else:
            baseline = run_and_grade(runtime, image, bundle, "").outcomes if with_baseline else None
            report = grade(
                runtime,
                image,
                bundle,
                result.patch,
                patch_is_none=patch_is_none,
                artifacts_dir=artifacts,
                baseline=baseline,
                reward_hack_policy=reward_hack_policy,
            )
        report.run_id = run_id
        report.solver = solver_spec  # self-identify the producing solver (golden vs a real LLM) in the artifact.
        report.image_digest = image.digest  # the exact image the verdict was produced against.
        report.config_hash = _config_hash(bundle, image)
        report.deps_hash = _deps_hash(artifacts / "deps.lock")  # hash the ACTUAL resolved deps (drift check).
        report.cost = _cost_from_meta(result.meta)  # token telemetry -> run.json's cost sidecar (empty for stubs).
        infra = infra_test_paths(result.patch)  # edge #1: did the solver touch test infra (possible gaming)?
        if infra and bundle.spec.kind != "test-synthesis":  # for synthesis, editing tests IS the task.
            verb = "reverted before grading" if reward_hack_policy is RewardHackPolicy.BLOCK else "left in place (WARN)"
            report.warnings.append(f"solver patch touches test infrastructure {infra} — {verb}")
        for finding in (*scan_outcome_override(result.patch), *scan_skip_injection(result.patch)):
            report.warnings.append(f"reward-hack signal: {finding}")
        write_run_json(report, artifacts / "run.json")
        _write_manifest(artifacts, run_id, bundle, image, solver.name, result.meta, report, result.context_files)
        _persist(store, run_id, command_id, bundle, image, solver.name, result.meta, report)
        if not keep_artifacts:
            _prune_bulky_artifacts(artifacts)
        return RunOutcome(run_id=run_id, report=report, artifacts_dir=artifacts)


_COST_KEYS = ("prompt_tokens", "completion_tokens", "reasoning_tokens")


def _cost_from_meta(meta: dict[str, str | int | float]) -> dict[str, float]:
    """Lift numeric token counts out of the solver meta into the run.json cost sidecar."""
    return {k: float(v) for k in _COST_KEYS if isinstance((v := meta.get(k)), (int, float))}


def _empty_patch_report() -> RunReport:
    """Not-resolved verdict for an empty patch, produced WITHOUT spinning a grade container (perf opt).

    An empty patch can never resolve (resolved requires patch_applied AND not patch_is_none), so grading
    is pure waste — `--skip-empty-grade` short-circuits it (SWE-PolyBench). `task validate` shows the baseline.
    """
    return RunReport(
        resolved=False,
        patch_exists=False,
        patch_applied=False,
        patch_is_none=True,
        buckets=TransitionBuckets(),
        warnings=["empty patch: grading skipped (--skip-empty-grade); run `task validate` for the baseline state"],
    )


def _prune_bulky_artifacts(artifacts: Path) -> None:
    """Drop the large solver-view tree snapshot; keep run.json / manifest / patch / logs (the eval artifact)."""
    view = artifacts / "solver_view"
    if view.is_dir():
        shutil.rmtree(view, ignore_errors=True)


def _write_manifest(
    artifacts: Path,
    run_id: str,
    bundle: Bundle,
    image: ResolvedImage,
    solver_name: str,
    meta: dict[str, str | int | float],
    report: RunReport,
    context_files: list[str],
) -> None:
    """Lifecycle manifest tying the three stages' artifacts together (the observability bonus)."""
    manifest = {
        "run_id": run_id,
        "task_id": bundle.spec.id,
        "solver": solver_name,
        "config_hash": report.config_hash,
        "stages": {
            "post_init": {
                "image_ref": image.ref,
                "image_digest": image.digest,
                "base_commit": bundle.spec.base_commit,
                "solver_view": "solver_view/",
            },
            "post_solver": {
                "patch": "solver.patch",
                "raw_response": "solver_response.md",  # model reasoning + proposed edits (if an LLM solver).
                "context_files": context_files,  # exactly which files the solver was shown (auditable retrieval).
                "meta": meta,
            },
            "post_grade": {
                "run_json": "run.json",
                "test_stdout": "test_stdout.log",
                "test_stderr": "test_stderr.log",
                "deps_lock": "deps.lock",
                "verdict": "resolved" if report.resolved else "not-resolved",
            },
        },
    }
    (artifacts / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")


def _config_hash(bundle: Bundle, image: ResolvedImage) -> str:
    """sha256 over the reproducibility-relevant inputs; same inputs -> same verdict on any machine."""
    spec = bundle.spec
    payload = json.dumps(
        {
            "schema_version": spec.schema_version,
            "image_digest": image.digest,
            "platform": image.platform,
            "base_commit": spec.base_commit,
            "run_cmd": spec.test.run_cmd,
            "parser": spec.test.parser,
            "selected_test_files": sorted(spec.test.selected_test_files),  # the test selector affects the verdict
            "setup_cmd": spec.test.setup_cmd,  # service/env setup the tests depend on
            "limits": {"cpus": spec.limits.cpus, "mem_mb": spec.limits.mem_mb, "pids": spec.limits.pids},
            "fail2pass": sorted(spec.buckets.fail2pass),
            "pass2pass": sorted(spec.buckets.pass2pass),
            "tool_version": __version__,
        },
        sort_keys=True,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _deps_hash(deps_lock: Path) -> str:
    """sha256 of the resolved dependency set (order-independent). Empty when deps weren't captured.

    Deliberately SEPARATE from config_hash: config_hash is the machine-independent bundle anchor (and the
    image is digest-pinned, so it already determines the deps); deps_hash records the set that was ACTUALLY
    installed, so the same bundle producing a different deps_hash across runs surfaces dependency drift.
    """
    try:
        lines = sorted(ln.strip() for ln in deps_lock.read_text(encoding="utf-8").splitlines() if ln.strip())
    except OSError:
        return ""
    return hashlib.sha256("\n".join(lines).encode("utf-8")).hexdigest() if lines else ""


def _persist(
    store: Store,
    run_id: str,
    command_id: str,
    bundle: Bundle,
    image: ResolvedImage,
    solver_name: str,
    meta: dict[str, str | int | float],
    report: RunReport,
) -> None:
    spec = bundle.spec
    fail2pass = set(spec.buckets.fail2pass)
    store.upsert_task(
        spec.id,
        spec.provenance.source,
        instance_id=spec.provenance.instance_id,
        repo=spec.repo,
        language=spec.language,
        domain=spec.domain,
        image_ref=image.ref,
        image_digest=image.digest,
    )
    store.record_run(
        RunRecord(
            run_id=run_id,
            command_id=command_id,
            task_id=spec.id,
            solver=solver_name,
            image_ref=image.ref,
            image_digest=image.digest,
            platform=image.platform,
            base_commit=spec.base_commit,
            verdict="resolved" if report.resolved else "not-resolved",
            resolved=report.resolved,
            patch_exists=report.patch_exists,
            patch_applied=report.patch_applied,
            patch_is_none=report.patch_is_none,
            cost_json=json.dumps(meta),
        )
    )
    rows = [
        ResultRow(test_id=tid, bucket="fail2pass" if tid in fail2pass else "pass2pass", outcome=status)
        for tid, status in report.per_test.items()
    ]
    store.record_test_results(run_id, rows)
