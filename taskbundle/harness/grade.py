"""Grade a solver's patch in a fresh, isolated container.

Order (the load-bearing correctness path): reset to base_commit -> apply the solver patch (multi-strategy)
-> stage the hidden tests (AFTER the patch, so the solver can't have touched them) -> run the declared
test command wrapped in sentinel markers -> parse the marker-delimited output -> compute the four-bucket
report. Runs in a container the solver never executed in.
"""

from __future__ import annotations

import json
import logging
import re
import shlex
from dataclasses import asdict, dataclass
from pathlib import Path

from taskbundle import constants
from taskbundle.bundle.loader import Bundle
from taskbundle.bundle.schema import HiddenSource
from taskbundle.containers.client import Container, DockerRuntime, HardenedConfig
from taskbundle.containers.protocol import ResolvedImage
from taskbundle.containers.tario import dir_to_tar, file_to_tar
from taskbundle.errors import GradeError
from taskbundle.harness.integrity import (
    RewardHackPolicy,
    infra_test_paths,
    reward_hack_revert_paths,
    touched_files,
)
from taskbundle.parsers import builtins as _builtins
from taskbundle.parsers.base import RunOutput, TestStatus
from taskbundle.parsers.registry import get_parser
from taskbundle.reporting import Group, RunReport, TransitionBuckets, compute_report

_LOG = logging.getLogger(__name__)
_builtins.load_builtins()  # register the built-in parsers.

_SOLVER_PATCH_NAME = "__taskbundle_solver.diff"
# Structured parser inputs + the raw run-script logs (the latter for debugging non-Python runs).
_ARTIFACT_FILES = ("output.json", "report.json", "sbp_stdout.log", "sbp_stderr.log")

# Per-language "what's the resolved dependency set?" command (best-effort; for the deps.lock artifact).
_DEP_LOCK_CMD = {
    "python": "pip freeze",
    "go": "go list -m all",
    "javascript": "npm ls --all 2>/dev/null || true",
    "js": "npm ls --all 2>/dev/null || true",
    "typescript": "npm ls --all 2>/dev/null || true",
    "ts": "npm ls --all 2>/dev/null || true",
}
_GRADE_SHELL = "/bin/bash"


@dataclass
class GradeRun:
    """Raw result of one grade pass: per-test outcomes + how the patch fared."""

    outcomes: dict[str, TestStatus]
    patch_applied: bool
    patch_exists: bool


@dataclass
class SynthesisReport:
    """Verdict for a synthesized TEST patch (SWT-Bench contract): fail->pass against the golden patch."""

    resolved: bool  # >=1 candidate test is fail->pass AND every candidate passes on the fixed state.
    fail_to_pass: list[str]  # candidate ids that failed on baseline and pass after the golden patch.
    pass_to_pass: list[str]  # candidate ids that passed in both states (well-formed but not reproducing).
    pre: dict[str, str]  # candidate id -> status on the buggy baseline.
    post: dict[str, str]  # candidate id -> status after the golden code patch.
    source_edits: list[str]  # non-test files the candidate touched (self-fix reward-hack) -> auto-fail.
    reason: str
    change_coverage: float | None = None  # dC: fraction of the golden patch's changed lines the test runs.


def run_and_grade(
    runtime: DockerRuntime,
    image: ResolvedImage,
    bundle: Bundle,
    solver_patch: str,
    *,
    artifacts_dir: Path | None = None,
    reward_hack_policy: RewardHackPolicy = RewardHackPolicy.BLOCK,
) -> GradeRun:
    """Apply `solver_patch`, stage hidden tests, run them, and parse per-test outcomes (no verdict).

    Edge-case #3: a totally empty runner result means the runner never ran (crash / image-init
    failure), not a legitimate all-fail verdict. We retry up to MAX_CONTAINER_RETRIES and, if it is
    still empty, raise GradeError — an infra failure must never be silently graded as 'not resolved'.

    `reward_hack_policy=BLOCK` (default) reverts every test-infra file the patch touches before the
    hidden tests run, so a patch-added conftest hook cannot rewrite outcomes (see `integrity`).
    """
    spec = bundle.spec
    config = HardenedConfig.for_grading(cpus=spec.limits.cpus, mem_mb=spec.limits.mem_mb, pids=spec.limits.pids)
    config.network = spec.test.grade_network  # some run_scripts (npm/redis) need network; default 'none'.
    patch_exists = bool(solver_patch.strip())

    patch_applied = False
    run_output: RunOutput | None = None
    deps = ""
    for attempt in range(1, constants.MAX_CONTAINER_RETRIES + 1):
        patch_applied, run_output, deps = _grade_attempt(
            runtime, image, bundle, solver_patch, patch_exists, config, reward_hack_policy
        )
        if not _is_infra_empty(run_output):
            break
        _LOG.warning(
            "grade attempt %d/%d produced no test output (suspected infra failure); retrying",
            attempt,
            constants.MAX_CONTAINER_RETRIES,
        )
    if run_output is None or _is_infra_empty(run_output):
        raise GradeError(
            f"test runner produced no output after {constants.MAX_CONTAINER_RETRIES} attempt(s); "
            "treating as an infrastructure failure, not a verdict"
        )

    if artifacts_dir is not None:
        _persist_test_output(artifacts_dir, run_output)
        if deps.strip():
            (artifacts_dir / "deps.lock").write_text(deps, encoding="utf-8")

    expected = list(spec.buckets.fail2pass) + list(spec.buckets.pass2pass)
    outcomes = get_parser(spec.test.parser).parse(run_output, expected)
    return GradeRun(outcomes=outcomes, patch_applied=patch_applied, patch_exists=patch_exists)


def _grade_attempt(
    runtime: DockerRuntime,
    image: ResolvedImage,
    bundle: Bundle,
    solver_patch: str,
    patch_exists: bool,
    config: HardenedConfig,
    reward_hack_policy: RewardHackPolicy,
) -> tuple[bool, RunOutput, str]:
    """One isolated grade pass in a fresh container: reset -> patch -> neutralize infra -> stage -> run."""
    spec = bundle.spec
    with runtime.container(image, config) as box:
        _reset_to_base(box, spec.base_commit, image.workdir)
        patch_applied = _apply_solver_patch(box, solver_patch, image.workdir) if patch_exists else False
        if patch_applied and reward_hack_policy is RewardHackPolicy.BLOCK:
            _neutralize_infra(box, solver_patch, spec.base_commit, image.workdir)
        _stage_hidden(box, bundle, image.workdir)
        _stage_scripts(box, bundle, image.workdir)
        run_output = _run_tests(box, bundle, image.workdir)
        deps = _capture_deps(box, spec.language, image.workdir)
    return patch_applied, run_output, deps


def _is_infra_empty(run_output: RunOutput) -> bool:
    """True when the runner produced no test output at all (empty marker section AND no artifacts).

    This is the 'runner never ran' signal (edge-case #3) — distinct from a real all-fail run, which
    still prints test output, and from id-drift (output present, ids mismatched), which `validate`
    surfaces as a warning rather than retrying.
    """
    has_section = bool(run_output.stdout.strip())
    has_artifacts = any(value.strip() for value in run_output.artifacts.values())
    return not (has_section or has_artifacts)


def grade(
    runtime: DockerRuntime,
    image: ResolvedImage,
    bundle: Bundle,
    solver_patch: str,
    *,
    patch_is_none: bool,
    artifacts_dir: Path | None = None,
    baseline: dict[str, TestStatus] | None = None,
    reward_hack_policy: RewardHackPolicy = RewardHackPolicy.BLOCK,
) -> RunReport:
    """Apply `solver_patch`, run the hidden tests, return the verdict. `baseline` populates F2F/P2F."""
    spec = bundle.spec
    result = run_and_grade(
        runtime, image, bundle, solver_patch, artifacts_dir=artifacts_dir, reward_hack_policy=reward_hack_policy
    )
    return compute_report(
        result.outcomes,
        list(spec.buckets.pass2pass),
        list(spec.buckets.fail2pass),
        patch_exists=result.patch_exists,
        patch_applied=result.patch_applied,
        patch_is_none=patch_is_none,
        baseline=baseline,
    )


def grade_test_synthesis(
    runtime: DockerRuntime,
    image: ResolvedImage,
    bundle: Bundle,
    test_patch: str,
    golden_patch: str,
    candidate_ids: list[str],
) -> SynthesisReport:
    """Score a synthesized TEST patch fail->pass against the golden CODE patch (SWT-Bench contract).

    The candidate must FAIL on the buggy baseline AND PASS after the golden patch is applied, contributing
    >=1 fail->pass and breaking nothing on the fixed state. The golden patch is the harness's held-out
    oracle -- the synthesizing solver never sees it, so this is the clean grade the generator-side papers
    only approximate. A candidate that edits non-test source is a self-fix reward-hack (it "fixes" the bug
    inside its own patch to satisfy fail->pass) and auto-fails. `candidate_ids` are the node ids the
    synthesized test introduces; deriving them from the patch via a collect-only pass is the next slice.
    """
    if not candidate_ids:
        return SynthesisReport(False, [], [], {}, {}, [], "no candidate test ids supplied")
    test_ish = set(infra_test_paths(test_patch))
    source_edits = [f for f in touched_files(test_patch) if f not in test_ish]
    files = sorted({cid.split("::")[0] for cid in candidate_ids})
    pre = _synthesis_run(runtime, image, bundle, [test_patch], files, candidate_ids)
    post = _synthesis_run(runtime, image, bundle, [golden_patch, test_patch], files, candidate_ids)
    f2p = [cid for cid in candidate_ids if pre[cid] is not TestStatus.PASSED and post[cid] is TestStatus.PASSED]
    p2p = [cid for cid in candidate_ids if pre[cid] is TestStatus.PASSED and post[cid] is TestStatus.PASSED]
    all_post_pass = all(post[cid] is TestStatus.PASSED for cid in candidate_ids)
    resolved = bool(f2p) and all_post_pass and not source_edits
    change_coverage = _synthesis_coverage(runtime, image, bundle, golden_patch, test_patch, files) if resolved else None
    return SynthesisReport(
        resolved=resolved,
        fail_to_pass=f2p,
        pass_to_pass=p2p,
        pre={cid: pre[cid].value for cid in candidate_ids},
        post={cid: post[cid].value for cid in candidate_ids},
        source_edits=source_edits,
        reason=_synthesis_reason(f2p, all_post_pass, source_edits),
        change_coverage=change_coverage,
    )


def grade_synthesis(
    runtime: DockerRuntime,
    image: ResolvedImage,
    bundle: Bundle,
    test_patch: str,
    *,
    artifacts_dir: Path | None = None,
) -> RunReport:
    """Full test-synthesis grade: discover the candidate's node ids, score fail->pass against the golden
    code patch (`bundle.patch`), and map the result to a RunReport for the shared run.json / DB plumbing."""
    candidate_ids = discover_candidate_ids(runtime, image, bundle, test_patch)
    synth = grade_test_synthesis(runtime, image, bundle, test_patch, bundle.patch or "", candidate_ids)
    if artifacts_dir is not None:
        (artifacts_dir / "synthesis.json").write_text(json.dumps(asdict(synth), indent=2), encoding="utf-8")
    return _synthesis_to_report(synth)


_COLLECTED_ID_RE = re.compile(r"^(\S+::\S+)\s*$", re.MULTILINE)


def discover_candidate_ids(runtime: DockerRuntime, image: ResolvedImage, bundle: Bundle, test_patch: str) -> list[str]:
    """Enumerate the node ids a synthesized test patch introduces via a collect-only pass in a fresh
    container (the SWT-Bench 'derive test directives from the patch' step). Empty if the bundle declares
    no `collect_cmd` or the patch adds no test file."""
    spec = bundle.spec
    files = infra_test_paths(test_patch)
    if not files or not spec.test.collect_cmd:
        return []
    command = spec.test.collect_cmd.replace("{test_files}", " ".join(shlex.quote(f) for f in files))
    config = HardenedConfig.for_grading(cpus=spec.limits.cpus, mem_mb=spec.limits.mem_mb, pids=spec.limits.pids)
    config.network = spec.test.grade_network
    with runtime.container(image, config) as box:
        _reset_to_base(box, spec.base_commit, image.workdir)
        _apply_solver_patch(box, test_patch, image.workdir)
        result = box.exec_shell(command, workdir=image.workdir, timeout_s=spec.limits.grade_wall_s, shell=_GRADE_SHELL)
    return _COLLECTED_ID_RE.findall(result.stdout)


# dC below this on a RESOLVED synthesis run flags a likely wrong-reason reproduction (Otter: true-F2P
# tests cover ~94% of changed lines, spurious ones ~60%). A flag, never a gate (detectors are flags).
_LOW_CHANGE_COVERAGE = 0.5


def _synthesis_to_report(synth: SynthesisReport) -> RunReport:
    """Map a SynthesisReport onto a RunReport so run.json / the DB / `task log` work unchanged."""
    warnings: list[str] = [] if synth.resolved else [synth.reason]  # only surface the reason on failure.
    if synth.source_edits:
        warnings.append(f"self-fix reward-hack: candidate edits non-test source {synth.source_edits}")
    if synth.change_coverage is not None:
        note = f"change-coverage dC={synth.change_coverage:.2f}"
        if synth.resolved and synth.change_coverage < _LOW_CHANGE_COVERAGE:
            note += " — LOW: reproduces but barely executes the fix (possible wrong-reason reproduction)"
        warnings.append(note)
    return RunReport(
        resolved=synth.resolved,
        patch_exists=bool(synth.post),
        patch_applied=True,
        patch_is_none=not synth.post,
        buckets=TransitionBuckets(
            fail_to_pass=Group(success=synth.fail_to_pass),
            pass_to_pass=Group(success=synth.pass_to_pass),
        ),
        per_test=synth.post,
        warnings=warnings,
    )


def _synthesis_run(
    runtime: DockerRuntime,
    image: ResolvedImage,
    bundle: Bundle,
    patches: list[str],
    files: list[str],
    candidate_ids: list[str],
) -> dict[str, TestStatus]:
    """Run the candidate test files with `patches` applied (code before test), parse the candidate ids."""
    spec = bundle.spec
    config = HardenedConfig.for_grading(cpus=spec.limits.cpus, mem_mb=spec.limits.mem_mb, pids=spec.limits.pids)
    config.network = spec.test.grade_network
    with runtime.container(image, config) as box:
        _reset_to_base(box, spec.base_commit, image.workdir)
        for patch in patches:
            if patch.strip():
                _apply_solver_patch(box, patch, image.workdir)
        run_output = _run_selected(box, bundle, files, image.workdir)
    return get_parser(spec.test.parser).parse(run_output, candidate_ids)


_HUNK_RE = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,\d+)? @@")


def changed_line_numbers(patch: str) -> dict[str, set[int]]:
    """Post-image line numbers each file's ADDED lines land on (the golden patch's 'changed lines')."""
    out: dict[str, set[int]] = {}
    current: str | None = None
    new_line = 0
    for line in patch.splitlines():
        if line.startswith("+++ b/"):
            current = line[len("+++ b/") :].strip()
            out.setdefault(current, set())
        elif line.startswith("@@"):
            m = _HUNK_RE.match(line)
            if m:
                new_line = int(m.group(1))
        elif current is None or not line:
            continue
        elif line[0] == "+" and not line.startswith("+++"):
            out[current].add(new_line)
            new_line += 1
        elif line[0] == " ":
            new_line += 1  # context line advances the new-file counter; '-' (removed) does not.
    return {path: lines for path, lines in out.items() if lines}


def _executed_lines(coverage_json: str, workdir: str) -> dict[str, set[int]]:
    """Parse a `coverage json` report into {repo-relative file -> executed line numbers}."""
    try:
        files = json.loads(coverage_json)["files"]
    except (json.JSONDecodeError, KeyError, TypeError):
        return {}
    prefix = workdir.strip("/") + "/"
    out: dict[str, set[int]] = {}
    for path, info in files.items():
        rel = path.replace("\\", "/").lstrip("/")
        rel = rel[len(prefix) :] if rel.startswith(prefix) else rel
        out[rel] = set(info.get("executed_lines", []))
    return out


def _change_coverage(golden_patch: str, executed: dict[str, set[int]]) -> float | None:
    """dC: fraction of the golden patch's changed lines that the candidate test executed. None if unknown."""
    changed = changed_line_numbers(golden_patch)
    total = sum(len(lines) for lines in changed.values())
    if total == 0:
        return None
    covered = sum(len(lines & executed.get(path, set())) for path, lines in changed.items())
    return covered / total


def _synthesis_coverage(
    runtime: DockerRuntime,
    image: ResolvedImage,
    bundle: Bundle,
    golden_patch: str,
    test_patch: str,
    files: list[str],
) -> float | None:
    """Run the candidate under coverage on the golden-patched state, compute dC. None if not measurable."""
    spec = bundle.spec
    if not spec.test.coverage_cmd or not golden_patch.strip():
        return None
    command = spec.test.coverage_cmd.replace("{test_files}", " ".join(shlex.quote(f) for f in files))
    config = HardenedConfig.for_grading(cpus=spec.limits.cpus, mem_mb=spec.limits.mem_mb, pids=spec.limits.pids)
    config.network = spec.test.grade_network
    with runtime.container(image, config) as box:
        _reset_to_base(box, spec.base_commit, image.workdir)
        _apply_solver_patch(box, golden_patch, image.workdir)
        _apply_solver_patch(box, test_patch, image.workdir)
        box.exec_shell(command, workdir=image.workdir, timeout_s=spec.limits.grade_wall_s, shell=_GRADE_SHELL)
        cov = box.exec(["cat", "coverage.json"], workdir=image.workdir, timeout_s=constants.DEFAULT_GRADE_WALL_S)
    if cov.exit_code != 0:
        return None
    return _change_coverage(golden_patch, _executed_lines(cov.stdout, image.workdir))


def _synthesis_reason(f2p: list[str], all_post_pass: bool, source_edits: list[str]) -> str:
    if source_edits:
        return f"candidate edits non-test source (self-fix reward-hack): {source_edits}"
    if not f2p:
        return "no candidate test is fail->pass (it does not reproduce the bug)"
    if not all_post_pass:
        return "a candidate test still fails after the golden patch (broken / regressing test)"
    return "reproduces: >=1 fail->pass and all candidate tests pass on the fixed state"


def _neutralize_infra(box: Container, patch: str, base_commit: str, workdir: str) -> None:
    """BLOCK policy: revert every test-infra / outcome-override file the patch touched, BEFORE the
    hidden tests run — so a patch-added conftest hook (or a bootstrap rebinding TestCase.run) cannot
    fire at collection and rewrite outcomes. Restore-to-base if the file existed there, else delete the
    solver-created file (the new-file case stock SWE-bench's reset misses, which is what makes it
    exploitable). Reverting an infra file never hides a real fix — the hidden tests are staged fresh.
    """
    base = shlex.quote(base_commit)
    for path in reward_hack_revert_paths(patch):
        q = shlex.quote(path)
        box.exec_shell(f"git checkout {base} -- {q} 2>/dev/null || rm -f {q}", workdir=workdir)


def _normalize_eol(text: str) -> str:
    """CRLF -> LF (edge #6). A Windows-authored patch carries \\r\\n; the container's repo is LF, so an
    un-normalized patch silently fails `git apply` ($'\\r': command not found / corrupt-patch reject)."""
    return text.replace("\r\n", "\n")


def _reset_to_base(box: Container, base_commit: str, workdir: str) -> None:
    base = shlex.quote(base_commit)
    # core.autocrlf=false: never let git rewrite EOLs on checkout — the verdict must not depend on the
    # host's autocrlf, and a CRLF-rewritten working tree would break patch application (edge #6).
    script = f"git config core.autocrlf false && git reset --hard {base} && git checkout {base} -- . && git clean -fd"
    result = box.exec_shell(script, workdir=workdir)
    if result.exit_code != 0:
        raise GradeError(f"could not reset repo to base_commit {base_commit}: {result.stderr or result.stdout}")


def _apply_solver_patch(box: Container, patch: str, workdir: str) -> bool:
    """Drop the patch into the container and try each apply strategy in turn. False = did not apply."""
    box.put_archive(workdir, file_to_tar(_SOLVER_PATCH_NAME, _normalize_eol(patch).encode("utf-8")))
    for strategy in constants.PATCH_APPLY_STRATEGIES:
        result = box.exec([*strategy, _SOLVER_PATCH_NAME], workdir=workdir)
        if result.exit_code == 0:
            return True
    return False


def _stage_hidden(box: Container, bundle: Bundle, workdir: str) -> None:
    """Stage the hidden grading tests — only here, after the solver patch is applied."""
    spec = bundle.spec
    if spec.test.hidden_source == HiddenSource.IMAGE_GIT:
        if spec.test.stage_hidden_cmd:
            result = box.exec_shell(spec.test.stage_hidden_cmd, workdir=workdir)
            if result.exit_code != 0:
                raise GradeError(f"could not stage hidden tests: {result.stderr or result.stdout}")
        return
    for bucket in ("fail2pass", "pass2pass"):  # bundle-files: overlay each bucket's repo-relative tree.
        bucket_dir = bundle.root / "tests" / bucket
        if bucket_dir.is_dir() and any(bucket_dir.iterdir()):
            box.put_archive(workdir, dir_to_tar(bucket_dir))


def _stage_scripts(box: Container, bundle: Bundle, workdir: str) -> None:
    """Stage Scale's run_script.sh / parser.py (if the bundle carries them) into the grade container."""
    for name in ("run_script.sh", "parser.py"):
        script = bundle.root / name
        if script.is_file():
            box.put_archive(workdir, file_to_tar(name, script.read_bytes()))


def _test_script(setup_cmd: str | None, command: str, start: str, end: str) -> str:
    """Build the grade shell snippet: optional in-shell service setup (edge #4), then marker-wrapped tests.

    setup_cmd runs in the SAME shell as the tests so a daemon it starts (redis/xvfb) stays up for them.
    """
    setup = f"{setup_cmd}; " if setup_cmd else ""
    return f"{setup}echo {shlex.quote(start)}; {command}; echo {shlex.quote(end)}"


def _run_tests(box: Container, bundle: Bundle, workdir: str) -> RunOutput:
    return _run_selected(box, bundle, bundle.spec.test.selected_test_files, workdir)


def _run_selected(box: Container, bundle: Bundle, files: list[str], workdir: str) -> RunOutput:
    """Run the declared test command over a specific file list (the synthesis path runs the candidate's)."""
    spec = bundle.spec
    test_files = " ".join(shlex.quote(f) for f in files)
    command = spec.test.run_cmd.replace("{test_files}", test_files)
    script = _test_script(spec.test.setup_cmd, command, constants.TEST_OUTPUT_START, constants.TEST_OUTPUT_END)
    result = box.exec_shell(script, workdir=workdir, timeout_s=spec.limits.grade_wall_s, shell=_GRADE_SHELL)
    return RunOutput(
        stdout=_between_markers(result.stdout),
        stderr=result.stderr,
        exit_code=result.exit_code,
        artifacts=_gather_artifacts(box, workdir),
    )


def _between_markers(text: str) -> str:
    """Extract the marker-delimited test section; fall back to full text if markers are missing."""
    start = text.find(constants.TEST_OUTPUT_START)
    end = text.find(constants.TEST_OUTPUT_END)
    if start != -1 and end > start:
        return text[start + len(constants.TEST_OUTPUT_START) : end]
    return text


def _gather_artifacts(box: Container, workdir: str) -> dict[str, str]:
    """Best-effort: pick up structured parser inputs (output.json / report.json) if the runner wrote them."""
    artifacts: dict[str, str] = {}
    for name in _ARTIFACT_FILES:
        result = box.exec(["cat", name], workdir=workdir, timeout_s=constants.EXEC_KILL_GRACE_S * 6)
        if result.exit_code == 0 and result.stdout.strip():
            artifacts[name] = result.stdout
    return artifacts


def _capture_deps(box: Container, language: str, workdir: str) -> str:
    """Best-effort resolved dependency set (pip freeze / go list / npm ls) for the deps.lock artifact."""
    command = _DEP_LOCK_CMD.get(language.lower())
    if not command:
        return ""
    result = box.exec_shell(command, workdir=workdir, timeout_s=constants.DEFAULT_GRADE_WALL_S)
    return result.stdout if result.exit_code == 0 else ""


def _persist_test_output(artifacts_dir: Path, run_output: RunOutput) -> None:
    """Save the test stdout/stderr + any structured artifacts (output.json) for debugging a run."""
    artifacts_dir.mkdir(parents=True, exist_ok=True)
    (artifacts_dir / "test_stdout.log").write_text(run_output.stdout, encoding="utf-8")
    (artifacts_dir / "test_stderr.log").write_text(run_output.stderr, encoding="utf-8")
    for name, content in run_output.artifacts.items():
        (artifacts_dir / name).write_text(content, encoding="utf-8")
