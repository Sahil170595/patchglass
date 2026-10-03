"""`task new`: scaffold a build-path custom bundle - the language-agnostic narrow-waist demo.

`init`/`validate`/`run` all operate on a bundle; this command produces a fresh, schema-valid one
from just (repo, commit, base image, build command) so an author can package an ARBITRARY repo
without hand-writing task.json. The image is a `build` spec (clone repo@commit + run build_cmd on a
digest-pinned base); hidden grading tests come from `tests/{fail2pass,pass2pass}/`, which the author
fills in. The skeleton validates against the schema but is intentionally NOT guardrail-complete -
`task validate` will refuse it until real hidden tests + buckets are added.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Final

from taskbundle.bundle.loader import DESCRIPTION_MD, PATCH_DIFF, TASK_JSON, load_bundle
from taskbundle.bundle.schema import Buckets, BuildImage, Provenance, TaskSpec, TestSpec
from taskbundle.errors import BundleValidationError, TaskBundleError

# language -> (run_cmd, parser). Only languages whose parser we've tested end-to-end get a default;
# authors of other languages must pass --test-cmd/--parser so we never silently ship a wrong runner.
_LANG_DEFAULTS: Final[dict[str, tuple[str, str]]] = {
    "python": ("python -m pytest {test_files} -rA -p no:cacheprovider", "pytest"),
}

_GITKEEP: Final = ".gitkeep"
_DESCRIPTION_TEMPLATE: Final = """\
# {task_id}

> TODO: replace this with the problem statement the SOLVER sees. Describe the bug or feature,
> the expected behavior, and any interface/constraints - but never reveal the hidden tests.

## Finishing this bundle

1. Drop the hidden grading tests into `tests/fail2pass/` (must FAIL on baseline) and
   `tests/pass2pass/` (must PASS on baseline), mirroring their repo-relative paths.
2. List their nodeids under `buckets.fail2pass` / `buckets.pass2pass` in `task.json`.
3. Add the visible test files the solver may run to `test.selected_test_files`.
4. (Optional) Put the golden patch in `patch.diff` so `task validate --patched` can prove it resolves.
5. Run `task init -b {out}` then `task validate -b {out}` to enforce the baseline guardrail.
"""


def new_bundle(
    out_dir: Path,
    *,
    task_id: str,
    repo: str,
    base_commit: str,
    base_image: str,
    build_cmd: str,
    language: str = "python",
    test_cmd: str | None = None,
    parser: str | None = None,
) -> Path:
    """Scaffold a build-path bundle skeleton at `out_dir`; round-trip validate before returning."""
    out = Path(out_dir)
    if out.exists() and any(out.iterdir()):
        raise BundleValidationError(f"refusing to scaffold into a non-empty directory: {out}")

    run_cmd, parser_name = _resolve_runner(language, test_cmd, parser)
    spec = TaskSpec(
        id=task_id,
        repo=repo,
        base_commit=base_commit,
        language=language.lower(),
        image=BuildImage(base_image=base_image, build_cmd=build_cmd),
        test=TestSpec(run_cmd=run_cmd, parser=parser_name),  # hidden_source defaults to bundle-files.
        buckets=Buckets(),
        provenance=Provenance(source="custom"),
    )

    out.mkdir(parents=True, exist_ok=True)
    task_json = json.dumps(spec.model_dump(mode="json"), indent=2, ensure_ascii=False) + "\n"
    (out / TASK_JSON).write_text(task_json, encoding="utf-8")
    (out / DESCRIPTION_MD).write_text(_DESCRIPTION_TEMPLATE.format(task_id=task_id, out=out), encoding="utf-8")
    (out / PATCH_DIFF).write_text("", encoding="utf-8")  # no oracle yet; author may add one.
    for bucket in ("fail2pass", "pass2pass"):
        bucket_dir = out / "tests" / bucket
        bucket_dir.mkdir(parents=True, exist_ok=True)
        (bucket_dir / _GITKEEP).write_text("", encoding="utf-8")

    try:
        load_bundle(out)  # the skeleton must itself be schema-valid (empty buckets are allowed here).
    except TaskBundleError as exc:
        raise BundleValidationError(f"scaffolded bundle at {out} failed validation: {exc}") from exc
    return out


def _resolve_runner(language: str, test_cmd: str | None, parser: str | None) -> tuple[str, str]:
    """Pick (run_cmd, parser): explicit flags win; else a tested per-language default; else error."""
    default = _LANG_DEFAULTS.get(language.lower())
    if test_cmd and parser:
        return test_cmd, parser
    if default is None:
        raise BundleValidationError(
            f"no built-in test runner default for language {language!r}; "
            "pass both --test-cmd and --parser explicitly"
        )
    return (test_cmd or default[0], parser or default[1])
