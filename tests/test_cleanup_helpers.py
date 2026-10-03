"""Offline tests for the consistency-cleanup helpers: run.json cost sidecar + digest-pin warning."""

from __future__ import annotations

from taskbundle.bundle.schema import BuildImage, PrebuiltImage
from taskbundle.harness.run import _cost_from_meta, _empty_patch_report
from taskbundle.harness.validate import image_digest_warning


def test_empty_patch_report_is_not_resolved_without_grading() -> None:
    report = _empty_patch_report()
    assert report.resolved is False and report.patch_is_none is True and report.patch_exists is False
    assert report.per_test == {}  # no container was spun, so no per-test outcomes
    assert any("grading skipped" in w for w in report.warnings)


def test_cost_from_meta_lifts_token_counts() -> None:
    meta: dict[str, str | int | float] = {
        "solver": "llm:openai/gpt-5.5",
        "edits": 4,
        "prompt_tokens": 1200,
        "completion_tokens": 800,
        "reasoning_tokens": 600,
    }
    assert _cost_from_meta(meta) == {"prompt_tokens": 1200.0, "completion_tokens": 800.0, "reasoning_tokens": 600.0}


def test_cost_from_meta_empty_for_stub_solver() -> None:
    assert _cost_from_meta({"solver": "golden", "edits": 1}) == {}


def test_digest_warning_fires_on_floating_tag() -> None:
    assert image_digest_warning(PrebuiltImage(ref="jefzda/sweap-images:sometag")) is not None


def test_digest_warning_silent_when_pinned_or_build() -> None:
    assert image_digest_warning(PrebuiltImage(ref="repo@sha256:abc123")) is None  # pinned in the ref
    assert image_digest_warning(PrebuiltImage(ref="repo:tag", digest="sha256:abc123")) is None  # recorded digest
    assert (
        image_digest_warning(BuildImage(base_image="python:3.12", build_cmd="pip install -e .")) is None
    )  # build path
