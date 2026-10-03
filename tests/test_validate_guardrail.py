"""validate_baseline enforcement (offline): a baseline guardrail violation must yield holds=False.

`grade` is monkeypatched so we exercise the enforcing branch without Docker — proving invariant #2
with a test (a fail2pass that PASSES on baseline, a pass2pass that FAILS, or a MISSING graded id all
fail the guardrail), per the repo's own "prove it with a test" bar.
"""

from __future__ import annotations

from pathlib import Path
from typing import cast

import pytest

from taskbundle.bundle.loader import Bundle
from taskbundle.bundle.schema import TaskSpec
from taskbundle.containers.client import DockerRuntime
from taskbundle.containers.protocol import ResolvedImage
from taskbundle.harness import validate as v
from taskbundle.reporting import Group, RunReport, TransitionBuckets

_RT = cast("DockerRuntime", object())
_IMG = ResolvedImage(ref="taskbundle-x:1", digest="", platform="linux/amd64", workdir="/app")  # local -> no digest gate


def _bundle(fail2pass: list[str], pass2pass: list[str]) -> Bundle:
    spec = TaskSpec.model_validate(
        {
            "id": "x",
            "repo": "r",
            "base_commit": "a" * 40,
            "image": {"kind": "prebuilt", "ref": "x:1"},
            "test": {"run_cmd": "pytest {test_files}", "parser": "pytest", "selected_test_files": []},
            "buckets": {"pass2pass": pass2pass, "fail2pass": fail2pass},
        }
    )
    return Bundle(root=Path("."), spec=spec, description="", patch=None, solution_test=None)


def _report(
    *, f2p_pass: list[str] | None = None, p2p_fail: list[str] | None = None, per_test: dict[str, str]
) -> RunReport:
    buckets = TransitionBuckets(fail_to_pass=Group(success=f2p_pass or []), pass_to_pass=Group(failure=p2p_fail or []))
    return RunReport(
        resolved=False, patch_exists=False, patch_applied=False, patch_is_none=True, buckets=buckets, per_test=per_test
    )


def test_fail2pass_passing_on_baseline_fails_the_guardrail(monkeypatch: pytest.MonkeyPatch) -> None:
    bundle = _bundle(["t.py::a"], ["t.py::b"])
    report = _report(f2p_pass=["t.py::a"], per_test={"t.py::a": "PASSED", "t.py::b": "PASSED"})
    monkeypatch.setattr(v, "grade", lambda *a, **k: report)
    result = v.validate_baseline(_RT, _IMG, bundle)
    assert result.holds is False
    assert any("fail2pass tests PASS on baseline" in m for m in result.messages)


def test_pass2pass_failing_on_baseline_fails_the_guardrail(monkeypatch: pytest.MonkeyPatch) -> None:
    bundle = _bundle(["t.py::a"], ["t.py::b"])
    report = _report(p2p_fail=["t.py::b"], per_test={"t.py::a": "FAILED", "t.py::b": "FAILED"})
    monkeypatch.setattr(v, "grade", lambda *a, **k: report)
    result = v.validate_baseline(_RT, _IMG, bundle)
    assert result.holds is False
    assert any("pass2pass tests FAIL on baseline" in m for m in result.messages)


def test_missing_fail2pass_fails_the_guardrail(monkeypatch: pytest.MonkeyPatch) -> None:
    bundle = _bundle(["t.py::a"], ["t.py::b"])
    report = _report(per_test={"t.py::a": "MISSING", "t.py::b": "PASSED"})
    monkeypatch.setattr(v, "grade", lambda *a, **k: report)
    result = v.validate_baseline(_RT, _IMG, bundle)
    assert result.holds is False
    assert any("never ran on baseline" in m for m in result.messages)


def test_well_formed_baseline_holds(monkeypatch: pytest.MonkeyPatch) -> None:
    bundle = _bundle(["t.py::a"], ["t.py::b"])
    report = _report(per_test={"t.py::a": "FAILED", "t.py::b": "PASSED"})  # f2p fails, p2p passes, none missing
    monkeypatch.setattr(v, "grade", lambda *a, **k: report)
    result = v.validate_baseline(_RT, _IMG, bundle)
    assert result.holds is True
