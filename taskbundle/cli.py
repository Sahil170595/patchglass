"""`task` CLI. Commands lazy-import the docker-touching harness so `--help`/`--version` stay fast.
Typed errors (BundleValidationError, GuardrailError, ...) are rendered as actionable messages, not
tracebacks, and map to a non-zero exit code."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer

from taskbundle import __version__
from taskbundle.errors import TaskBundleError

app = typer.Typer(
    name="task",
    help="Containerized SWE-bench-style task bundles: init, validate, run a solver, query results.",
    no_args_is_help=True,
    add_completion=False,
)

_BundleOpt = Annotated[Path, typer.Option("--bundle", "-b", help="Bundle directory.")]


def _fail(message: str) -> None:
    typer.secho(f"error: {message}", fg=typer.colors.RED, err=True)
    raise typer.Exit(code=1)


def _version_callback(value: bool) -> None:
    if value:
        typer.echo(f"taskbundle {__version__}")
        raise typer.Exit()


@app.callback()
def main(
    _version: Annotated[
        bool, typer.Option("--version", callback=_version_callback, is_eager=True, help="Show version and exit.")
    ] = False,
) -> None:
    """Task Bundle CLI."""


@app.command()
def new(
    out: Annotated[Path, typer.Option("--out", "-o", help="Output bundle directory (must be new/empty).")],
    task_id: Annotated[str, typer.Option("--id", help="Stable task id.")],
    repo: Annotated[str, typer.Option("--repo", help="Repository URL.")],
    commit: Annotated[str, typer.Option("--commit", help="Base commit SHA (7-40 hex).")],
    base_image: Annotated[str, typer.Option("--base-image", help="Base image (digest-pin recommended).")],
    build_cmd: Annotated[
        str, typer.Option("--build-cmd", help="Build command run after clone (e.g. 'pip install -e .').")
    ],
    language: Annotated[str, typer.Option("--language", help="Informational; selects test defaults.")] = "python",
    test_cmd: Annotated[
        str | None,
        typer.Option("--test-cmd", help="Test command ('{test_files}' templated); required for non-default langs."),
    ] = None,
    parser: Annotated[
        str | None, typer.Option("--parser", help="Parser name (e.g. 'pytest'); required for non-default langs.")
    ] = None,
) -> None:
    """Scaffold a build-path custom bundle from (repo, commit, base image, build cmd)."""
    from taskbundle.bundle.scaffold import new_bundle
    from taskbundle.harness import services

    try:
        path = new_bundle(
            out,
            task_id=task_id,
            repo=repo,
            base_commit=commit,
            base_image=base_image,
            build_cmd=build_cmd,
            language=language,
            test_cmd=test_cmd,
            parser=parser,
        )
    except TaskBundleError as exc:
        _fail(str(exc))
        return
    # Scaffold succeeded; record the command into the freshly-created bundle (can't pre-create its dir).
    with services.command_log(path, "new", {"id": task_id, "repo": repo, "commit": commit}, bundle_id=task_id):
        pass
    typer.secho(f"scaffolded bundle at {path}", fg=typer.colors.GREEN)
    typer.echo("  next: add hidden tests under tests/{fail2pass,pass2pass}/, fill buckets in task.json,")
    typer.echo(f"        then: task init -b {path} && task validate -b {path}")


@app.command()
def init(bundle: _BundleOpt) -> None:
    """Materialize the baseline: obtain the env image and record its digest."""
    from taskbundle.harness.init import init_bundle

    try:
        result = init_bundle(bundle)
    except TaskBundleError as exc:
        _fail(str(exc))
        return
    typer.secho(f"initialized task {result.bundle.spec.id}", fg=typer.colors.GREEN)
    typer.echo(f"  image:  {result.image.ref}")
    typer.echo(f"  digest: {result.image.digest}")
    typer.echo(f"  platform: {result.image.platform}")


@app.command()
def validate(
    bundle: _BundleOpt,
    patched: Annotated[bool, typer.Option("--patched", help="Apply golden patch and assert it resolves.")] = False,
    check_flaky: Annotated[
        bool, typer.Option("--check-flaky", help="Run baseline N times (FLAKY_CHECK_RUNS); flag tests that flip.")
    ] = False,
    check_gameable: Annotated[
        bool, typer.Option("--check-gameable", help="Empty patch must keep every fail2pass genuinely failing.")
    ] = False,
) -> None:
    """Enforcing guardrail: on baseline, all pass2pass pass AND all fail2pass fail (else non-zero)."""
    from taskbundle.bundle.loader import load_bundle
    from taskbundle.containers.client import DockerRuntime
    from taskbundle.containers.image import provider_for
    from taskbundle.harness import services
    from taskbundle.harness import validate as v

    args: dict[str, object] = {"patched": patched, "check_flaky": check_flaky, "check_gameable": check_gameable}
    try:
        loaded = load_bundle(bundle)
        with services.command_log(bundle, "validate", args, bundle_id=loaded.spec.id):
            runtime = DockerRuntime()
            image = provider_for(loaded.spec, runtime).ensure_image()
            if loaded.spec.kind == "test-synthesis":
                # Synthesis has no baseline fail2pass buckets; the guardrail is that the golden test reproduces.
                synth = v.validate_synthesis(runtime, image, loaded)
                for message in synth.messages:
                    typer.echo(f"  {message}")
                if not synth.holds:
                    _fail("synthesis guardrail violated: golden reproducing test is not fail->pass")
                typer.secho("validate: OK", fg=typer.colors.GREEN)
                return
            result = (
                v.validate_patched(runtime, image, loaded) if patched else v.validate_baseline(runtime, image, loaded)
            )
            for message in result.messages:
                typer.echo(f"  {message}")
            if check_gameable and not patched:
                gameable = v.validate_gameable(runtime, image, loaded)
                for message in gameable.messages:
                    typer.echo(f"  {message}")
                if not gameable.holds:
                    _fail("task is gameable (see messages above)")
            if check_flaky and not patched:
                from taskbundle import constants

                runs = [result.report.per_test]
                for _ in range(constants.FLAKY_CHECK_RUNS - 1):  # N total baseline runs (matches the paper).
                    runs.append(v.validate_baseline(runtime, image, loaded).report.per_test)
                flippers = sorted(tid for tid in runs[0] if any(r.get(tid) != runs[0][tid] for r in runs[1:]))
                if flippers:
                    _fail(
                        f"flaky tests (outcome changed across {constants.FLAKY_CHECK_RUNS} baseline runs): {flippers}"
                    )
            if not result.holds:
                _fail("guardrail violated (see messages above)")
    except TaskBundleError as exc:
        _fail(str(exc))
        return
    typer.secho("validate: OK", fg=typer.colors.GREEN)


@app.command()
def run(
    bundle: _BundleOpt,
    solver: Annotated[
        str, typer.Option("--solver", help="golden | noop | cmd:'…' | llm:ollama|openai|huggingface/<model>")
    ] = "golden",
    baseline: Annotated[
        bool, typer.Option("--baseline", "-B", help="Also grade baseline -> populate FAIL_TO_FAIL/PASS_TO_FAIL.")
    ] = False,
    solver_network: Annotated[
        str | None, typer.Option("--solver-network", help="Override command-solver network: none | bridge.")
    ] = None,
    keep_artifacts: Annotated[
        bool,
        typer.Option("--keep-artifacts/--no-keep-artifacts", help="Keep the solver-view snapshot (default) or prune."),
    ] = True,
    reasoning_effort: Annotated[
        str | None,
        typer.Option(
            "--reasoning-effort", help="Reasoning effort for reasoning LLMs (e.g. gpt-5.5: low|medium|high|xhigh)."
        ),
    ] = None,
    localize: Annotated[
        bool, typer.Option("--localize", help="LLM solver: rank+select relevant files instead of a flat dump.")
    ] = False,
    localize_budget: Annotated[
        int | None,
        typer.Option("--localize-budget", help="Max bytes of localized context (tighten to fit a model's TPM)."),
    ] = None,
    max_attempts: Annotated[
        int, typer.Option("--max-attempts", help="LLM solver: re-prompt up to N times if the patch doesn't apply.")
    ] = 1,
    skip_empty_grade: Annotated[
        bool, typer.Option("--skip-empty-grade", help="Perf: skip the grade container when the solver patch is empty.")
    ] = False,
    fail_on_unresolved: Annotated[
        bool, typer.Option("--fail-on-unresolved", help="Exit non-zero if the task is not resolved (CI gate).")
    ] = False,
    reward_hack_policy: Annotated[
        str,
        typer.Option("--reward-hack-policy", help="Solver edits to test infra: block (revert, default) | warn (flag)."),
    ] = "block",
) -> None:
    """Run the solver, grade in a fresh container, emit run.json + DB rows."""
    from taskbundle.harness.integrity import RewardHackPolicy
    from taskbundle.harness.run import run_task
    from taskbundle.reporting import render_table

    try:
        policy = RewardHackPolicy(reward_hack_policy)
    except ValueError:
        _fail(f"--reward-hack-policy must be 'block' or 'warn', got {reward_hack_policy!r}")
        return
    try:
        outcome = run_task(
            bundle,
            solver,
            with_baseline=baseline,
            solver_network=solver_network,
            keep_artifacts=keep_artifacts,
            reasoning_effort=reasoning_effort,
            localize=localize,
            localize_budget=localize_budget,
            max_attempts=max_attempts,
            skip_empty_grade=skip_empty_grade,
            reward_hack_policy=policy,
        )
    except TaskBundleError as exc:
        _fail(str(exc))
        return
    color = typer.colors.GREEN if outcome.report.resolved else typer.colors.YELLOW
    typer.secho(render_table(outcome.report), fg=color)
    for warning in outcome.report.warnings:
        typer.secho(f"warning: {warning}", fg=typer.colors.YELLOW, err=True)
    typer.echo(f"\nrun_id: {outcome.run_id}")
    typer.echo(f"artifacts: {outcome.artifacts_dir}")
    if fail_on_unresolved and not outcome.report.resolved:
        _fail(f"task not resolved (run {outcome.run_id})")


@app.command()
def log(
    target_id: Annotated[str, typer.Argument(help="A run id or command id.")],
    bundle: _BundleOpt = Path("."),
) -> None:
    """Query the DB: per-test breakdown for a run, or the log for a command."""
    from taskbundle.harness import services

    store = services.open_store(bundle)
    try:
        run_row = store.get_run(target_id)
        if run_row is not None:
            _print_run(run_row)
            return
        command = store.get_command(target_id)
        if command is None:
            _fail(f"no run or command with id {target_id!r} in {services.data_dir(bundle)}")
            return
        typer.echo(f"command {command.id}: {command.type} [{command.status}] {command.started_at}")
        if command.error:
            typer.echo(f"  error: {command.error}")
    finally:
        store.close()


@app.command()
def ls(
    bundle: _BundleOpt = Path("."),
    limit: Annotated[int, typer.Option("--limit", "-n", help="Max rows.")] = 20,
) -> None:
    """List recent commands from the DB."""
    from taskbundle.harness import services

    store = services.open_store(bundle)
    try:
        rows = store.recent_commands(limit)
    finally:
        store.close()
    if not rows:
        typer.echo("(no commands recorded)")
        return
    for row in rows:
        typer.echo(f"{row.started_at}  {row.type:<9} {row.status:<8} {row.id}")


@app.command(name="import")
def import_(
    instance_id: Annotated[str, typer.Argument(help="SWE-bench Pro instance id.")],
    out: Annotated[Path, typer.Option("--out", "-o", help="Output bundle directory.")],
) -> None:
    """Import a SWE-bench Pro instance into a bundle."""
    from taskbundle.harness import services
    from taskbundle.importer.swebench_pro import SweBenchProAdapter

    try:
        with services.command_log(out, "import", {"instance_id": instance_id}, bundle_id=instance_id):
            path = SweBenchProAdapter().to_bundle(instance_id, out)
    except TaskBundleError as exc:
        _fail(str(exc))
        return
    typer.secho(f"wrote bundle to {path}", fg=typer.colors.GREEN)


@app.command()
def doctor() -> None:
    """Preflight: docker reachable, seccomp present, platform == linux/amd64."""
    from taskbundle.harness.doctor import critical_ok, run_doctor

    checks = run_doctor()
    for check in checks:
        mark = "OK " if check.ok else "!! "
        color = typer.colors.GREEN if check.ok else typer.colors.YELLOW
        typer.secho(f"{mark}{check.name}", fg=color, nl=False)
        typer.echo(f" - {check.detail}")
    if not critical_ok(checks):
        _fail("a critical preflight check failed (see above) — fix before running tasks")


@app.command()
def diff(
    run_id: Annotated[str, typer.Argument(help="A run id.")],
    bundle: _BundleOpt = Path("."),
) -> None:
    """Show the solver's produced patch for a run, plus the baseline->patched test delta."""
    from taskbundle.harness import services
    from taskbundle.reporting import RunReport, render_table

    patch = services.data_dir(bundle) / "runs" / run_id / "solver.patch"
    if not patch.is_file():
        _fail(f"no solver patch for run {run_id!r} at {patch}")
        return
    typer.echo(patch.read_text(encoding="utf-8") or "(empty patch)")
    run_json = patch.parent / "run.json"
    if run_json.is_file():
        report = RunReport.model_validate_json(run_json.read_text(encoding="utf-8"))
        typer.echo("\n--- test delta (baseline -> patched) ---")
        typer.echo(render_table(report))


suite_app = typer.Typer(help="Run a matrix of (bundle x solver) and aggregate the results.", no_args_is_help=True)
app.add_typer(suite_app, name="suite")


@suite_app.command("run")
def suite_run(
    bundle: Annotated[list[Path], typer.Option("--bundle", "-b", help="Bundle dir (repeatable).")],
    solver: Annotated[list[str] | None, typer.Option("--solver", help="Solver (repeatable; default golden).")] = None,
    name: Annotated[str, typer.Option("--name", help="Suite name.")] = "suite",
    workers: Annotated[int, typer.Option("--workers", "-w", help="Concurrent runs.")] = 2,
    store: Annotated[Path, typer.Option("--store", help="Where the shared suite DB lives.")] = Path("."),
    resume: Annotated[bool, typer.Option("--resume", help="Skip cells already resolved in the store.")] = False,
) -> None:
    """Run every (bundle x solver) cell concurrently, grouped under one suite id."""
    from taskbundle.harness import services
    from taskbundle.harness.suite import run_suite

    solvers = solver or ["golden"]
    args: dict[str, object] = {"name": name, "solvers": solvers, "bundles": [str(b) for b in bundle]}
    try:
        with services.command_log(store, "suite", args):
            result = run_suite(bundle, solvers, name=name, store_dir=store, workers=workers, resume=resume)
    except TaskBundleError as exc:
        _fail(str(exc))
        return
    typer.secho(
        f"suite {result.suite_id}: {result.resolved_count}/{len(result.results)} resolved", fg=typer.colors.GREEN
    )
    for cell in result.results:
        mark = "skip" if cell.skipped else ("OK " if cell.resolved else ("ERR" if cell.error else "no "))
        line = f"  {mark} {cell.solver:<22} {cell.task_id[:46]}"
        typer.echo(line + (f"  ({cell.error[:60]})" if cell.error else ""))
    typer.echo(f"\nreport with: task report --suite {result.suite_id} --by language --store {store}")


@app.command()
def report(
    by: Annotated[str, typer.Option("--by", help="Group by: solver|model|task|language|domain|repo|bench.")] = "solver",
    suite: Annotated[str | None, typer.Option("--suite", help="Limit to one suite id.")] = None,
    store: Annotated[Path, typer.Option("--store", help="Where the suite DB lives.")] = Path("."),
) -> None:
    """Aggregate resolve-rate (Pass@1) over runs by a chosen dimension."""
    from taskbundle import constants
    from taskbundle.db.store import Store
    from taskbundle.harness import services

    db = Store(services.data_dir(store) / constants.DB_FILENAME)
    try:
        rates = db.resolve_rates(suite_id=suite, group_by=by)
    except ValueError as exc:
        db.close()
        _fail(str(exc))
        return
    db.close()
    if not rates:
        typer.echo("(no runs recorded)")
        return
    typer.echo(f"{'group':<30} {'resolved':>9} {'total':>6} {'rate':>7}")
    for row in rates:
        typer.echo(f"{row.group[:30]:<30} {row.resolved:>9} {row.total:>6} {row.rate:>7.1%}")


def _print_run(run_row: dict[str, object]) -> None:
    verdict = "RESOLVED" if run_row.get("resolved") else "NOT RESOLVED"
    typer.echo(f"run {run_row.get('run_id')}: {verdict}  (solver={run_row.get('solver')})")
    typer.echo(f"  image: {run_row.get('image_ref')}")
    typer.echo(f"  digest: {run_row.get('image_digest')}")
    results = run_row.get("test_results")
    if isinstance(results, list):
        for entry in results:
            typer.echo(f"  [{entry['bucket']:<9}] {entry['outcome']:<8} {entry['test_id']}")


def run_cli() -> None:
    """Entry point: run the app, converting any UNEXPECTED exception into a clean actionable error.

    Commands already render typed TaskBundleErrors; this is the backstop so a docker-SDK error, KeyError,
    etc. exits non-zero with a one-line message (fail loud) instead of dumping a raw traceback at the user.
    """
    try:
        app()
    except TaskBundleError as exc:  # belt-and-suspenders (commands normally catch these themselves).
        _fail(str(exc))
    except Exception as exc:
        typer.secho(f"error: unexpected failure: {type(exc).__name__}: {exc}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1) from exc


if __name__ == "__main__":
    run_cli()
