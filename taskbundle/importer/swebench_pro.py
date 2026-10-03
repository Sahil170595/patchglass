"""SWE-bench Pro adapter: HF dataset row -> validated bundle.

The mapping (`row_to_bundle`) is offline: a field-map plus `json.loads`. Network fetching
is injectable, so mapping and protocol behavior can be tested without a remote dataset.
SWE-bench Pro uses per-instance prebuilt images
(`jefzda/sweap-images:{tag}`) and stages hidden tests by `git checkout {instance_commit}` at
grade time (`hidden_source = image-git`), so this adapter never carries hidden test FILES.
"""

from __future__ import annotations

import ast
import json
import logging
import re
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Iterable
from pathlib import Path
from typing import Any, Final, Protocol, cast

from taskbundle import constants
from taskbundle.bundle.loader import DESCRIPTION_MD, PATCH_DIFF, TASK_JSON, load_bundle
from taskbundle.bundle.schema import (
    Buckets,
    HiddenSource,
    PrebuiltImage,
    Provenance,
    SolverNetwork,
    TaskSpec,
    TestSpec,
)
from taskbundle.errors import BundleValidationError, TaskBundleError

_LOG: Final = logging.getLogger(__name__)

# --- Adapter identity / declared test surface ------------------------------------------------
ADAPTER_NAME: Final = "swebench-pro"  # adapter registry key.
PROVENANCE_SOURCE: Final = "swebench-pro"  # stamped into Provenance.source for reporting joins.

# Scale runs each instance via a per-image `run_script.sh` (tests -> logs), then `parser.py`
# (logs -> output.json, which the `scale_run_script` parser reads). We encode both steps in run_cmd;
# the harness stages run_script.sh + parser.py into the grade container. Those run_scripts do
# npm install / start redis etc., so the GRADE container for them needs network (the solver stays
# isolated). For Python repos we instead invoke pytest directly through our tested pytest parser
# (network-isolated grade, fewer moving parts; test ids are already pytest nodeids).
PARSER_NAME: Final = "scale_run_script"
SCALE_RUN_CMD: Final = (
    # `< /dev/null` gives the script a valid stdin fd: docker exec has none, and some test runners
    # (e.g. tutanota's `npm run build`) crash reading an undefined process.stdin without it.
    "bash run_script.sh {test_files} < /dev/null > sbp_stdout.log 2> sbp_stderr.log; "
    "python3 parser.py sbp_stdout.log sbp_stderr.log output.json"
)
RUN_SCRIPT_FILES: Final = ("run_script.sh", "parser.py")
RUN_SCRIPTS_BASE: Final = "https://raw.githubusercontent.com/scaleapi/SWE-bench_Pro-os/main/run_scripts"

# language -> (run_cmd, parser, grade_network).
_LANG_RUNNER: Final[dict[str, tuple[str, str, SolverNetwork]]] = {
    "python": ("python -m pytest {test_files} -rA -p no:cacheprovider", "pytest", SolverNetwork.NONE),
}


def _runner_for(language: str) -> tuple[str, str, SolverNetwork]:
    # Default (js/go/ts/…): Scale's run_script + parser; grading needs network (npm/redis/etc.).
    return _LANG_RUNNER.get(language.lower(), (SCALE_RUN_CMD, PARSER_NAME, SolverNetwork.BRIDGE))


# --- HF datasets-server REST paging (network path only) --------------------------------------
HF_CONFIG: Final = "default"  # SWE-bench Pro exposes a single "default" config.
HF_SPLIT: Final = "test"  # the public graded split.
HF_PAGE_LEN: Final = 100  # datasets-server caps `length` at 100 rows/request.
HF_FETCH_TIMEOUT_S: Final = 30  # bounded so a hung endpoint surfaces as a typed error, not a stall.

# `before_repo_set_cmd` mixes env noise (`cd`, `export`, `source`) with the staging line(s).
# Only `git checkout <commit> -- <paths>` actually stages hidden tests; keep those, drop the rest.
_GIT_CHECKOUT_RE: Final = re.compile(r"^\s*git\s+checkout\b.*?--\s+\S")


class _Opener(Protocol):
    """Minimal urlopen-shaped seam so tests can inject an offline opener."""

    def __call__(self, url: str, *, timeout: float) -> Any: ...


def _coerce_str_list(raw: str, *, field: str) -> list[str]:
    """Parse a JSON-encoded list field into a flat list of nodeid strings.

    SWE-bench Pro is inconsistent: a bucket may be a JSON list of nodeids, a JSON list whose elements
    are sub-lists OR Python-repr-of-list strings (grouped/parametrized nodeids), or a bare label.
    All of these normalize to a flat list of strings here.
    """
    text = raw.strip()
    if not text:
        return []
    try:
        value = json.loads(text)
    except json.JSONDecodeError:
        listish = _try_literal_list(text)  # python-repr list (single quotes), or a bare label.
        if listish is not None:
            return _flatten_str_list(listish)
        _LOG.debug("field %s is not JSON/list (%r); treating as single bare value", field, text)
        return [text]
    if not isinstance(value, list):
        raise BundleValidationError(f"row field {field!r} must decode to a JSON list, got {type(value).__name__}")
    return _flatten_str_list(value)


def _try_literal_list(text: str) -> list[object] | None:
    """Return the list a Python-repr string denotes (e.g. "['a', 'b']"), else None."""
    stripped = text.strip()
    if not (stripped.startswith("[") and stripped.endswith("]")):
        return None
    try:
        parsed = ast.literal_eval(stripped)
    except (ValueError, SyntaxError):
        return None
    return parsed if isinstance(parsed, list) else None


def _flatten_str_list(value: list[object]) -> list[str]:
    """Flatten sub-lists and Python-repr-of-list strings into a flat list of nodeid strings."""
    out: list[str] = []
    for item in value:
        if isinstance(item, list):
            out.extend(_flatten_str_list(item))
        elif isinstance(item, str) and (inner := _try_literal_list(item)) is not None:
            out.extend(_flatten_str_list(inner))
        else:
            out.append(str(item))
    return out


def _derive_stage_hidden_cmd(before_repo_set_cmd: str) -> str | None:
    """Keep only the `git checkout {instance_commit} -- <test files>` staging line(s).

    `before_repo_set_cmd` is the image-git staging script; everything except the checkout lines
    (cd/export/source) is environment setup the grader already handles, so it is dropped.
    """
    lines = [line.strip() for line in before_repo_set_cmd.splitlines() if _GIT_CHECKOUT_RE.match(line)]
    return "\n".join(lines) if lines else None


def _build_description(problem_statement: str, requirements: str, interface: str) -> str:
    """Solver-visible problem text; append Requirements/Interface sections only when non-empty."""
    parts: list[str] = [problem_statement.rstrip()]
    if requirements.strip():
        parts.append(f"## Requirements\n\n{requirements.strip()}")
    if interface.strip():
        parts.append(f"## Interface\n\n{interface.strip()}")
    return "\n\n".join(parts) + "\n"


def _build_spec(row: dict[str, str]) -> TaskSpec:
    """Map an HF row to a TaskSpec (no I/O). KeyError surfaces a missing-column as a typed error."""
    try:
        image = PrebuiltImage(
            ref=f"{constants.SWEBENCH_PRO_IMAGE_PREFIX}:{row['dockerhub_tag']}",
            workdir=constants.DEFAULT_WORKDIR,
        )
        run_cmd, parser, grade_network = _runner_for(row["repo_language"])
        test = TestSpec(
            run_cmd=run_cmd,
            parser=parser,
            selected_test_files=_coerce_str_list(row["selected_test_files_to_run"], field="selected_test_files_to_run"),
            stage_hidden_cmd=_derive_stage_hidden_cmd(row["before_repo_set_cmd"]),
            hidden_source=HiddenSource.IMAGE_GIT,
            grade_network=grade_network,
        )
        buckets = Buckets(
            pass2pass=_coerce_str_list(row["pass_to_pass"], field="pass_to_pass"),
            fail2pass=_coerce_str_list(row["fail_to_pass"], field="fail_to_pass"),
        )
        provenance = Provenance(
            source=PROVENANCE_SOURCE,
            instance_id=row["instance_id"],
            issue_categories=_coerce_str_list(row.get("issue_categories", ""), field="issue_categories"),
        )
        return TaskSpec(
            id=row["instance_id"],
            repo=row["repo"],
            base_commit=row["base_commit"],
            language=row["repo_language"].lower(),
            image=image,
            test=test,
            buckets=buckets,
            provenance=provenance,
        )
    except KeyError as exc:
        raise BundleValidationError(f"SWE-bench Pro row missing required column: {exc.args[0]!r}") from exc


def row_to_bundle(row: dict[str, str], out_dir: Path) -> Path:
    """Materialize one HF row as a bundle directory (PURE, OFFLINE). Returns the bundle path.

    Writes task.json / description.md / patch.diff, then re-loads through the frozen loader to
    prove the generated bundle validates (fail-fast, never a half-written bundle).
    """
    out = Path(out_dir)
    spec = _build_spec(row)
    description = _build_description(
        row.get("problem_statement", ""),
        row.get("requirements", ""),
        row.get("interface", ""),
    )

    out.mkdir(parents=True, exist_ok=True)
    task_json = json.dumps(spec.model_dump(mode="json"), indent=2, ensure_ascii=False) + "\n"
    (out / TASK_JSON).write_text(task_json, encoding="utf-8")
    (out / DESCRIPTION_MD).write_text(description, encoding="utf-8")
    (out / PATCH_DIFF).write_text(row.get("patch", ""), encoding="utf-8")

    try:
        load_bundle(out)  # round-trip validation: the generated bundle MUST load cleanly.
    except TaskBundleError as exc:
        raise BundleValidationError(
            f"generated SWE-bench Pro bundle for {spec.id!r} failed validation at {out}: {exc}"
        ) from exc
    return out


def _hf_rows_url(*, offset: int, length: int) -> str:
    """Build a datasets-server `/rows` URL for the SWE-bench Pro test split."""
    query = urllib.parse.urlencode(
        {
            "dataset": constants.SWEBENCH_PRO_DATASET,
            "config": HF_CONFIG,
            "split": HF_SPLIT,
            "offset": offset,
            "length": length,
        }
    )
    return f"{constants.HF_DATASETS_SERVER}/rows?{query}"


def _fetch_page(offset: int, length: int, opener: _Opener) -> list[dict[str, str]]:
    """Fetch one page of rows from the HF datasets-server (network). Returns the row dicts."""
    url = _hf_rows_url(offset=offset, length=length)
    try:
        with opener(url, timeout=HF_FETCH_TIMEOUT_S) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except urllib.error.URLError as exc:
        raise TaskBundleError(f"HF datasets-server unreachable for {url}: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise TaskBundleError(f"HF datasets-server returned non-JSON for {url}: {exc}") from exc
    rows = payload.get("rows")
    if not isinstance(rows, list):
        raise TaskBundleError(f"HF datasets-server response missing 'rows' list for {url}")
    # Each entry is {"row_idx", "row": {...}, "truncated_cells": [...]}; we want the inner row.
    return [cast("dict[str, str]", entry["row"]) for entry in rows]


def fetch_row(instance_id: str, *, opener: _Opener = urllib.request.urlopen) -> dict[str, str]:
    """Fetch the row whose `instance_id` matches, paging the HF datasets-server REST API (network).

    Thin + injectable: tests pass a stub `opener` to stay offline. Raises if the id is not found.
    """
    offset = 0
    while True:
        page = _fetch_page(offset, HF_PAGE_LEN, opener)
        if not page:
            break
        for row in page:
            if row.get("instance_id") == instance_id:
                return row
        offset += HF_PAGE_LEN
    raise BundleValidationError(f"instance_id {instance_id!r} not found in {constants.SWEBENCH_PRO_DATASET}")


def _iter_ids(opener: _Opener) -> Iterable[str]:
    """Yield instance ids by paging the dataset (network). Stops at the first empty page."""
    offset = 0
    while True:
        page = _fetch_page(offset, HF_PAGE_LEN, opener)
        if not page:
            return
        for row in page:
            instance_id = row.get("instance_id")
            if instance_id:
                yield instance_id
        offset += HF_PAGE_LEN


class SweBenchProAdapter:
    """BenchAdapter for ScaleAI/SWE-bench_Pro. Pure mapping + a thin, injectable HF fetcher."""

    name: str = ADAPTER_NAME

    def __init__(
        self,
        *,
        fetch: Callable[[str], dict[str, str]] | None = None,
        opener: _Opener = urllib.request.urlopen,
    ) -> None:
        """`fetch`/`opener` are injectable so the adapter is fully testable offline (no I/O in init)."""
        self._opener = opener
        self._fetch = fetch if fetch is not None else (lambda iid: fetch_row(iid, opener=opener))

    def iter_instances(self) -> Iterable[str]:
        """Yield available instance ids (network: pages the HF datasets-server)."""
        return _iter_ids(self._opener)

    def to_bundle(self, instance_id: str, out_dir: Path) -> Path:
        """Fetch the row -> validated bundle; for non-Python tasks also fetch Scale's run scripts."""
        row = self._fetch(instance_id)
        path = row_to_bundle(row, out_dir)
        if _runner_for(row.get("repo_language", ""))[1] == PARSER_NAME:
            self._fetch_run_scripts(instance_id, path)
        return path

    def _fetch_run_scripts(self, instance_id: str, out_dir: Path) -> None:
        """Fetch Scale's per-instance run_script.sh + parser.py into the bundle (network)."""
        for fname in RUN_SCRIPT_FILES:
            url = f"{RUN_SCRIPTS_BASE}/{instance_id}/{fname}"
            try:
                with self._opener(url, timeout=HF_FETCH_TIMEOUT_S) as resp:
                    content = resp.read().decode("utf-8")
            except urllib.error.URLError as exc:
                raise TaskBundleError(f"could not fetch {fname} for {instance_id} from Scale: {exc}") from exc
            # Force LF: these are shell/python scripts run inside a Linux container; CRLF (Windows
            # default for write_text) makes bash choke ("$'\r': command not found"). Edge-case #6.
            (out_dir / fname).write_text(content.replace("\r\n", "\n"), encoding="utf-8", newline="\n")
