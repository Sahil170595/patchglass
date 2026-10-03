"""SWE-bench Pro importer contract — row_to_bundle is pure/offline and produces a valid bundle.

No test touches the real network: the HF fetch/paging seam is exercised through an injected
offline `opener` (the same seam the adapter exposes for testability). Both the pure `row_to_bundle`
mapping and the error branches (non-list bucket JSON, missing column, instance-id not found) are
covered (HF fields are ALL strings, JSON-encoded lists).
"""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any, Literal

import pytest

from taskbundle import constants
from taskbundle.bundle.loader import load_bundle
from taskbundle.bundle.schema import HiddenSource, PrebuiltImage
from taskbundle.errors import BundleValidationError
from taskbundle.importer.swebench_pro import (
    SweBenchProAdapter,
    fetch_row,
    row_to_bundle,
)

# --- Independently authored dataset-protocol row (strings and JSON-encoded lists) ---
_BASE_COMMIT = "1" * 40  # synthetic SHA; no upstream task is distributed.
_INSTANCE_COMMIT = "2" * 40  # synthetic hidden-test source commit.
_DOCKERHUB_TAG = "sample.widget-22222222"  # synthetic protocol-compatible image tag.
_FAIL_TO_PASS = ["tests/unit/utils/test_policy.py::TestPolicy::test_primary"]
_PASS_TO_PASS = [
    "tests/unit/utils/test_log.py::TestStable::test_positive",
    "tests/unit/utils/test_log.py::TestStable::test_zero",
]
_SELECTED_TEST_FILES = [
    "tests/unit/utils/test_log.py",
    "tests/unit/utils/test_policy.py",
]
_PROBLEM_STATEMENT = "The widget filter ignores OMIT_TAGS; honor that configuration."


def _canned_row() -> dict[str, str]:
    """A SWE-bench Pro HF row: ALL fields strings; list fields JSON-encoded."""
    return {
        "instance_id": "sample__widget-22222222",
        "repo": "https://github.com/example/widget",
        "base_commit": _BASE_COMMIT,
        "patch": "--- a/widget/policy.py\n+++ b/widget/policy.py\n@@\n-old\n+new\n",
        "test_patch": "--- a/tests/unit/utils/test_policy.py\n+++ b/tests/unit/utils/test_policy.py\n@@\n+new test\n",
        "problem_statement": _PROBLEM_STATEMENT,
        "requirements": "The FilterPolicy must respect OMIT_TAGS.",
        "interface": "def apply_policy(tags: list[str]) -> list[str]: ...",
        "repo_language": "Python",
        "fail_to_pass": json.dumps(_FAIL_TO_PASS),
        "pass_to_pass": json.dumps(_PASS_TO_PASS),
        "selected_test_files_to_run": json.dumps(_SELECTED_TEST_FILES),
        "before_repo_set_cmd": (
            "cd /app\n"
            f"git checkout {_INSTANCE_COMMIT} -- tests/unit/utils/test_log.py tests/unit/utils/test_policy.py\n"
        ),
        "dockerhub_tag": _DOCKERHUB_TAG,
        "issue_categories": json.dumps(["logging", "bug-fix"]),
    }


def test_row_to_bundle_produces_loadable_bundle(tmp_path: Path) -> None:
    out = row_to_bundle(_canned_row(), tmp_path / "bundle")
    bundle = load_bundle(out)  # round-trips through the frozen loader/schema.

    assert bundle.spec.id == "sample__widget-22222222"
    assert bundle.spec.repo == "https://github.com/example/widget"
    assert bundle.spec.base_commit == _BASE_COMMIT
    assert bundle.spec.language == "python"  # lower-cased from "Python".


def test_image_ref_uses_prefix_and_tag(tmp_path: Path) -> None:
    out = row_to_bundle(_canned_row(), tmp_path / "bundle")
    spec = load_bundle(out).spec
    assert isinstance(spec.image, PrebuiltImage)
    assert spec.image.ref == f"{constants.SWEBENCH_PRO_IMAGE_PREFIX}:{_DOCKERHUB_TAG}"
    assert spec.image.ref == f"jefzda/sweap-images:{_DOCKERHUB_TAG}"
    assert spec.image.workdir == "/app"


def test_buckets_parsed_from_json_strings(tmp_path: Path) -> None:
    out = row_to_bundle(_canned_row(), tmp_path / "bundle")
    spec = load_bundle(out).spec
    assert spec.buckets.fail2pass == _FAIL_TO_PASS
    assert spec.buckets.pass2pass == _PASS_TO_PASS


def test_test_spec_hidden_source_is_image_git(tmp_path: Path) -> None:
    out = row_to_bundle(_canned_row(), tmp_path / "bundle")
    spec = load_bundle(out).spec
    assert spec.test.hidden_source == HiddenSource.IMAGE_GIT
    assert spec.test.parser == "pytest"  # Python rows use the tested pytest parser directly
    assert spec.test.selected_test_files == _SELECTED_TEST_FILES
    assert "{test_files}" in spec.test.run_cmd


def test_non_python_language_falls_back_to_scale_run_script(tmp_path: Path) -> None:
    row = {**_canned_row(), "repo_language": "JavaScript"}
    spec = load_bundle(row_to_bundle(row, tmp_path / "bundle")).spec
    assert spec.test.parser == "scale_run_script"
    assert "run_script.sh" in spec.test.run_cmd
    assert "parser.py" in spec.test.run_cmd  # two-step: run_script -> logs -> parser.py -> output.json
    assert spec.test.grade_network == "bridge"  # run_scripts need network (npm/redis); solver stays isolated


def test_nested_bucket_nodeids_are_flattened(tmp_path: Path) -> None:
    row = {**_canned_row(), "fail_to_pass": json.dumps([["a.py::t1", "a.py::t2"], "b.py::t3"])}
    spec = load_bundle(row_to_bundle(row, tmp_path / "bundle")).spec
    assert spec.buckets.fail2pass == ["a.py::t1", "a.py::t2", "b.py::t3"]


def test_repr_string_bucket_is_expanded(tmp_path: Path) -> None:
    # A JSON array may contain a Python-repr-of-list string.
    row = {**_canned_row(), "fail_to_pass": json.dumps(["['a.py::t1', 'a.py::t2']"])}
    spec = load_bundle(row_to_bundle(row, tmp_path / "bundle")).spec
    assert spec.buckets.fail2pass == ["a.py::t1", "a.py::t2"]


def test_stage_hidden_cmd_keeps_only_git_checkout(tmp_path: Path) -> None:
    out = row_to_bundle(_canned_row(), tmp_path / "bundle")
    spec = load_bundle(out).spec
    assert spec.test.stage_hidden_cmd is not None
    # The 'cd /app' line is environment noise; only the test-staging checkout is retained.
    assert spec.test.stage_hidden_cmd.startswith("git checkout")
    assert _INSTANCE_COMMIT in spec.test.stage_hidden_cmd
    assert "cd /app" not in spec.test.stage_hidden_cmd


def test_description_contains_problem_and_sections(tmp_path: Path) -> None:
    out = row_to_bundle(_canned_row(), tmp_path / "bundle")
    description = (out / "description.md").read_text(encoding="utf-8")
    assert _PROBLEM_STATEMENT in description
    assert "## Requirements" in description
    assert "OMIT_TAGS" in description
    assert "## Interface" in description


def test_description_omits_empty_optional_sections(tmp_path: Path) -> None:
    row = _canned_row()
    row["requirements"] = ""
    row["interface"] = "   "  # whitespace-only counts as empty.
    out = row_to_bundle(row, tmp_path / "bundle")
    description = (out / "description.md").read_text(encoding="utf-8")
    assert _PROBLEM_STATEMENT in description
    assert "## Requirements" not in description
    assert "## Interface" not in description


def test_patch_diff_is_written_verbatim(tmp_path: Path) -> None:
    row = _canned_row()
    out = row_to_bundle(row, tmp_path / "bundle")
    assert (out / "patch.diff").read_text(encoding="utf-8") == row["patch"]


def test_provenance_records_source_and_categories(tmp_path: Path) -> None:
    out = row_to_bundle(_canned_row(), tmp_path / "bundle")
    spec = load_bundle(out).spec
    assert spec.provenance.source == "swebench-pro"
    assert spec.provenance.instance_id == "sample__widget-22222222"
    assert spec.provenance.issue_categories == ["logging", "bug-fix"]


def test_issue_categories_accepts_plain_string(tmp_path: Path) -> None:
    row = _canned_row()
    row["issue_categories"] = "logging"  # some rows carry a bare label, not JSON.
    out = row_to_bundle(row, tmp_path / "bundle")
    spec = load_bundle(out).spec
    assert spec.provenance.issue_categories == ["logging"]


def test_adapter_name_and_protocol(tmp_path: Path) -> None:
    adapter = SweBenchProAdapter()
    assert adapter.name == "swebench-pro"
    # to_bundle delegates to a fetcher; inject a stub so the test stays fully offline.
    row = _canned_row()
    adapter_with_stub = SweBenchProAdapter(fetch=lambda instance_id: row)
    out = adapter_with_stub.to_bundle(row["instance_id"], tmp_path / "viastub")
    assert load_bundle(out).spec.id == row["instance_id"]


def test_fetch_is_not_called_without_network() -> None:
    # Guard: constructing the adapter must not perform any I/O.
    SweBenchProAdapter()
    # Nothing to assert beyond "no exception / no network" — the absence of a fetch call is the point.


# --- Error branches: each BundleValidationError raise must be exercised, not assumed -----------


def test_non_list_bucket_json_is_rejected(tmp_path: Path) -> None:
    # A JSON object (not a list) in a list field is a malformed row, not a single bare label.
    row = _canned_row()
    row["pass_to_pass"] = json.dumps({"not": "a list"})
    with pytest.raises(BundleValidationError, match="must decode to a JSON list"):
        row_to_bundle(row, tmp_path / "bundle")


def test_missing_required_column_is_typed_error(tmp_path: Path) -> None:
    row = _canned_row()
    del row["base_commit"]  # drop a required HF column.
    with pytest.raises(BundleValidationError, match="missing required column.*base_commit"):
        row_to_bundle(row, tmp_path / "bundle")


# --- Offline HF fetch/paging seam (injected opener; no real network) ---------------------------


class _FakeResp:
    """urlopen-shaped context manager returning a canned JSON page body."""

    def __init__(self, payload: dict[str, Any]) -> None:
        self._body = json.dumps(payload).encode("utf-8")

    def __enter__(self) -> _FakeResp:
        return self

    def __exit__(self, *_: object) -> Literal[False]:
        return False

    def read(self) -> bytes:
        return self._body


def _paging_opener(pages: list[dict[str, Any]]) -> Callable[..., _FakeResp]:
    """Return an opener that serves `pages` in order, then empty pages (paging terminator)."""
    calls = {"n": 0}

    def opener(url: str, *, timeout: float) -> _FakeResp:
        idx = calls["n"]
        calls["n"] += 1
        payload = pages[idx] if idx < len(pages) else {"rows": []}
        return _FakeResp(payload)

    return opener


def _page(*instance_ids: str) -> dict[str, Any]:
    """HF datasets-server /rows shape: {"rows": [{"row_idx", "row": {...}}, ...]}."""
    return {"rows": [{"row_idx": i, "row": {"instance_id": iid}} for i, iid in enumerate(instance_ids)]}


def test_fetch_row_not_found_raises(tmp_path: Path) -> None:
    opener = _paging_opener([_page("a", "b")])
    with pytest.raises(BundleValidationError, match="not found"):
        fetch_row("does-not-exist", opener=opener)


def test_fetch_row_spans_multiple_pages() -> None:
    # Target lives on the 2nd page: the loop must advance offset, not stop at page 1.
    opener = _paging_opener([_page("a", "b"), _page("c", "target")])
    assert fetch_row("target", opener=opener)["instance_id"] == "target"


def test_iter_instances_pages_offline() -> None:
    opener = _paging_opener([_page("a", "b"), _page("c")])
    adapter = SweBenchProAdapter(opener=opener)
    assert list(adapter.iter_instances()) == ["a", "b", "c"]


def test_to_bundle_uses_injected_opener_end_to_end(tmp_path: Path) -> None:
    # Full adapter path with NO real network: opener serves the canned row, then a validated bundle.
    row = _canned_row()
    opener = _paging_opener([{"rows": [{"row_idx": 0, "row": row}]}])
    adapter = SweBenchProAdapter(opener=opener)
    out = adapter.to_bundle(row["instance_id"], tmp_path / "e2e")
    assert load_bundle(out).spec.id == row["instance_id"]
