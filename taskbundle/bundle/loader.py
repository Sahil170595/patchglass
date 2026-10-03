"""Load + validate a bundle directory into a typed Bundle. Errors are actionable, not tracebacks."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from pydantic import ValidationError

from taskbundle.bundle.schema import TaskSpec
from taskbundle.errors import BundleNotFoundError, BundleValidationError

TASK_JSON: str = "task.json"
DESCRIPTION_MD: str = "description.md"
PATCH_DIFF: str = "patch.diff"
SOLUTION_TEST_DIFF: str = "solution_test.diff"


@dataclass(frozen=True)
class Bundle:
    """A loaded bundle: validated spec + resolved paths to its components."""

    root: Path
    spec: TaskSpec
    description: str
    patch: str | None  # golden patch; may be absent (custom tasks without an oracle).
    solution_test: str | None  # test-synthesis: the golden reproducing test (for the synth-golden stub).

    @property
    def task_json_path(self) -> Path:
        return self.root / TASK_JSON


def load_bundle(path: str | Path) -> Bundle:
    """Load and validate a bundle directory. Raises BundleNotFoundError / BundleValidationError."""
    root = Path(path)
    if not root.is_dir():
        raise BundleNotFoundError(f"bundle directory not found: {root}")

    task_json = root / TASK_JSON
    if not task_json.is_file():
        raise BundleNotFoundError(f"missing {TASK_JSON} in bundle {root}")

    try:
        raw = json.loads(task_json.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise BundleValidationError(f"{task_json} is not valid JSON: {exc}") from exc

    try:
        spec = TaskSpec.model_validate(raw)
    except ValidationError as exc:
        raise BundleValidationError(f"{task_json} failed schema validation:\n{exc}") from exc

    desc_path = root / DESCRIPTION_MD
    if not desc_path.is_file():
        raise BundleNotFoundError(f"missing {DESCRIPTION_MD} in bundle {root}")
    description = desc_path.read_text(encoding="utf-8")

    patch_path = root / PATCH_DIFF
    patch = patch_path.read_text(encoding="utf-8") if patch_path.is_file() else None

    solution_path = root / SOLUTION_TEST_DIFF
    solution_test = solution_path.read_text(encoding="utf-8") if solution_path.is_file() else None

    return Bundle(root=root, spec=spec, description=description, patch=patch, solution_test=solution_test)
