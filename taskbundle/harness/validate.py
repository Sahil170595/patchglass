"""`task validate`: the enforcing guardrail.

Baseline (default): on the unpatched repo, every pass2pass MUST pass AND every fail2pass MUST fail.
A fail2pass that passes on baseline (doesn't capture the bug) or a pass2pass that fails (broken
baseline) is a HARD error — the task itself is malformed.
--patched: apply the golden patch and assert it resolves the task (fail2pass pass, pass2pass stay).
"""

from __future__ import annotations

from dataclasses import dataclass

from taskbundle.bundle.loader import Bundle
from taskbundle.bundle.schema import ImageSpec, PrebuiltImage, is_registry_ref
from taskbundle.containers.client import DockerRuntime
from taskbundle.containers.protocol import ResolvedImage
from taskbundle.errors import GuardrailError
from taskbundle.harness.grade import grade, grade_synthesis
from taskbundle.parsers.base import TestStatus
from taskbundle.reporting import RunReport


@dataclass
class ValidateResult:
    holds: bool
    report: RunReport
    messages: list[str]


def image_digest_warning(spec_image: ImageSpec) -> str | None:
    """Flag a prebuilt image referenced by a floating tag with no recorded digest."""
    if isinstance(spec_image, PrebuiltImage) and not spec_image.digest and "@sha256:" not in spec_image.ref:
        return "image is not digest-pinned in task.json (run `task init` to record the digest for reproducibility)"
    return None


def validate_baseline(runtime: DockerRuntime, image: ResolvedImage, bundle: Bundle) -> ValidateResult:
    """Run both buckets on the unpatched baseline and check the guardrail invariant."""
    report = grade(runtime, image, bundle, "", patch_is_none=True)
    errors: list[str] = []
    warnings: list[str] = []

    digest_issue = image_digest_warning(bundle.spec.image)
    if digest_issue:
        # A namespaced registry image can and must be digest-pinned -> a gating error (run `task init`).
        # A bare local/built image cannot be portably pinned, so it stays an advisory warning.
        spec_image = bundle.spec.image
        if isinstance(spec_image, PrebuiltImage) and is_registry_ref(spec_image.ref):
            errors.append(f"{digest_issue} — the resolved image is {image.digest}")
        else:
            warnings.append(f"warning: {digest_issue}")
    if not bundle.spec.buckets.fail2pass:
        errors.append("degenerate task: no fail2pass tests (nothing to verify)")
    missing = sorted(tid for tid, status in report.per_test.items() if status == TestStatus.MISSING.value)
    if missing:
        # A graded id that never ran on baseline (wrong/renamed id, uncollected test, or a build failure)
        # means the guardrail cannot be confirmed — every fail2pass must genuinely FAIL and every pass2pass
        # genuinely PASS, and MISSING is neither. Strict by default, not an advisory.
        errors.append(
            f"{len(missing)} graded test id(s) never ran on baseline (the guardrail can't hold): {missing[:3]}"
        )

    if report.buckets.pass_to_pass.failure:
        errors.append(f"pass2pass tests FAIL on baseline (must pass): {report.buckets.pass_to_pass.failure[:3]}")
    if report.buckets.fail_to_pass.success:
        errors.append(f"fail2pass tests PASS on baseline (must fail): {report.buckets.fail_to_pass.success[:3]}")

    if errors:
        return ValidateResult(holds=False, report=report, messages=warnings + errors)
    return ValidateResult(
        holds=True,
        report=report,
        messages=[*warnings, "baseline guardrail holds: all pass2pass pass and all fail2pass fail"],
    )


def validate_gameable(runtime: DockerRuntime, image: ResolvedImage, bundle: Bundle) -> ValidateResult:
    """`--check-gameable`: with NO patch, every fail2pass must genuinely FAIL (not pass, not be missing).

    Stronger than the baseline check: it rejects a task that is winnable by doing nothing (a fail2pass
    that already passes) AND one whose fail2pass never runs (MISSING/SKIPPED) — which can't prove the
    test actually gates the fix. Either way an empty patch must not look like progress.
    """
    report = grade(runtime, image, bundle, "", patch_is_none=True)
    really_failing = {TestStatus.FAILED.value, TestStatus.ERROR.value}
    winnable = report.buckets.fail_to_pass.success  # fail2pass that PASS with no fix -> gameable.
    not_failing = sorted(
        tid
        for tid in bundle.spec.buckets.fail2pass
        if tid not in winnable and report.per_test.get(tid) not in really_failing
    )
    messages: list[str] = []
    holds = True
    if winnable:
        holds = False
        messages.append(f"gameable: fail2pass PASS with no patch (winnable by doing nothing): {winnable[:3]}")
    if not_failing:
        holds = False
        messages.append(f"fail2pass not genuinely failing (MISSING/SKIPPED — cannot prove it gates): {not_failing[:3]}")
    if holds:
        messages.append("not gameable: every fail2pass genuinely fails on the empty patch")
    return ValidateResult(holds=holds, report=report, messages=messages)


def validate_synthesis(runtime: DockerRuntime, image: ResolvedImage, bundle: Bundle) -> ValidateResult:
    """Test-synthesis guardrail (invariant #2 for `kind='test-synthesis'`): the bundle's own golden
    reproducing test must itself score fail->pass against the golden code patch. A synthesis bundle whose
    golden test does not reproduce the bug is malformed -- TDD-Bench drops ~10% of instances this way.
    """
    if not bundle.solution_test:
        raise GuardrailError("test-synthesis validate needs a solution_test.diff (the golden reproducing test)")
    if not bundle.patch:
        raise GuardrailError("test-synthesis validate needs a patch.diff (the golden code patch = the oracle)")
    report = grade_synthesis(runtime, image, bundle, bundle.solution_test)
    if report.resolved:
        return ValidateResult(True, report, ["golden reproducing test is fail->pass against the golden patch"])
    return ValidateResult(False, report, report.warnings or ["golden reproducing test does not reproduce the bug"])


def validate_patched(runtime: DockerRuntime, image: ResolvedImage, bundle: Bundle) -> ValidateResult:
    """Apply the golden patch and assert it resolves the task (validates the patch + task)."""
    if not bundle.patch:
        raise GuardrailError("--patched requires a patch.diff in the bundle")
    report = grade(runtime, image, bundle, bundle.patch, patch_is_none=False)
    if report.resolved:
        return ValidateResult(True, report, ["golden patch resolves the task (fail2pass now pass, pass2pass intact)"])
    detail = []
    if report.buckets.fail_to_pass.failure:
        detail.append(f"fail2pass still failing: {report.buckets.fail_to_pass.failure}")
    if report.buckets.pass_to_pass.failure:
        detail.append(f"pass2pass regressed: {report.buckets.pass_to_pass.failure}")
    return ValidateResult(False, report, detail or ["golden patch did not resolve the task"])
