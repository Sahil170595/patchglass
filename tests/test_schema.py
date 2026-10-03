"""TaskSpec schema contract — valid loads, invalid rejected with typed errors."""

from __future__ import annotations

import copy

import pytest
from pydantic import ValidationError

from taskbundle.bundle.schema import BuildImage, PrebuiltImage, TaskSpec

VALID: dict[str, object] = {
    "id": "demo-1",
    "repo": "https://github.com/octo/widget",
    "base_commit": "a" * 40,
    "image": {"kind": "prebuilt", "ref": "jefzda/sweap-images:demo"},
    "test": {"run_cmd": "pytest {test_files}", "parser": "pytest", "selected_test_files": ["t.py"]},
    "buckets": {"pass2pass": ["t.py::test_a"], "fail2pass": ["t.py::test_b"]},
}


def test_valid_prebuilt() -> None:
    spec = TaskSpec.model_validate(VALID)
    assert spec.id == "demo-1"
    assert isinstance(spec.image, PrebuiltImage)
    assert spec.image.platform == "linux/amd64"  # default applied
    assert spec.limits.grade_wall_s == 1800  # named-constant default


def test_valid_build_image() -> None:
    data = copy.deepcopy(VALID)
    data["image"] = {"kind": "build", "base_image": "python:3.12-slim", "build_cmd": "pip install -e ."}
    spec = TaskSpec.model_validate(data)
    assert isinstance(spec.image, BuildImage)


def test_overlapping_buckets_rejected() -> None:
    data = copy.deepcopy(VALID)
    data["buckets"] = {"pass2pass": ["t.py::x"], "fail2pass": ["t.py::x"]}
    with pytest.raises(ValidationError, match="overlap"):
        TaskSpec.model_validate(data)


def test_bad_base_commit_rejected() -> None:
    data = copy.deepcopy(VALID)
    data["base_commit"] = "not-a-sha"
    with pytest.raises(ValidationError, match="base_commit"):
        TaskSpec.model_validate(data)


def test_unknown_image_kind_rejected() -> None:
    data = copy.deepcopy(VALID)
    data["image"] = {"kind": "bogus", "ref": "x"}
    with pytest.raises(ValidationError):
        TaskSpec.model_validate(data)


def test_extra_field_rejected() -> None:
    data = copy.deepcopy(VALID)
    data["surprise"] = 1
    with pytest.raises(ValidationError):
        TaskSpec.model_validate(data)


def test_wrong_schema_version_rejected() -> None:
    data = copy.deepcopy(VALID)
    data["schema_version"] = 99
    with pytest.raises(ValidationError, match="schema_version"):
        TaskSpec.model_validate(data)


def test_grade_network_constrained_to_none_or_bridge() -> None:
    # the GRADE container's network is enum-constrained: a task.json cannot request 'host'.
    base_test = {"run_cmd": "pytest {test_files}", "parser": "pytest", "selected_test_files": ["t.py"]}
    data = copy.deepcopy(VALID)
    data["test"] = {**base_test, "grade_network": "host"}
    with pytest.raises(ValidationError):
        TaskSpec.model_validate(data)
    for ok in ("none", "bridge"):
        data["test"] = {**base_test, "grade_network": ok}
        assert TaskSpec.model_validate(data).test.grade_network.value == ok
