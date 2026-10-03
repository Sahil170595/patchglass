"""Extract a baseline working tree and remove recognized hidden-test content and Git metadata.

Symbol matching scans declared paths and test-looking files, including relocated definitions and
bare Go names. Path/title identifiers are handled by removing referenced files. Whole-file removal
can also remove co-located visible tests. Recognized residual content raises IsolationError.

Name/path heuristics and bounded reads are not a universal confidentiality proof: non-test-looking
files, aliases, unusual runner collection, unreadable content, or indirect disclosure may escape
detection. The separate command solver uses its image rather than this sanitized host snapshot.
"""

from __future__ import annotations

import re
import shutil
from collections.abc import Iterator
from pathlib import Path
from typing import Final

from taskbundle.bundle.loader import Bundle
from taskbundle.containers.client import DockerRuntime, HardenedConfig
from taskbundle.containers.protocol import ResolvedImage
from taskbundle.containers.tario import extract_tar
from taskbundle.errors import IsolationError

# A graded test source is small; skip reading pathologically large files when scanning for symbols.
_MAX_TEST_FILE_BYTES: Final = 4_000_000
# Source extensions whose test files we scan/strip (the languages the harness grades).
_CODE_EXTS: Final = ("py", "go", "js", "jsx", "ts", "tsx", "rb", "java")
_PATH_RE: Final = re.compile(r"[\w./-]+\.(?:" + "|".join(_CODE_EXTS) + r")\b")
_IDENT_RE: Final = re.compile(r"[A-Za-z_]\w*")
# Template matching a definition of a class/function named <name> in Python/JS/Go.
_DEFINES_TMPL: Final = r"(?m)^[ \t]*(?:export\s+)?(?:class|def|func|function)\s+{name}\b"
_TEST_DIRS: Final = frozenset({"test", "tests", "spec", "specs", "__tests__"})
_TEST_NAME_RE: Final = re.compile(
    r"(?:^|/)(?:test_[^/]*|conftest)\.py$|_test\.(?:py|go)$|\.(?:test|spec)\.(?:js|jsx|ts|tsx)$",
    re.IGNORECASE,
)


def materialize_solver_view(runtime: DockerRuntime, image: ResolvedImage, bundle: Bundle, dest: Path) -> Path:
    """Export base_commit tree to `dest`, strip history + graded tests, prove no hidden test remains."""
    spec = bundle.spec
    config = HardenedConfig.for_grading(cpus=spec.limits.cpus, mem_mb=spec.limits.mem_mb, pids=spec.limits.pids)
    reset = f"git reset --hard {spec.base_commit} && git checkout {spec.base_commit} -- . && git clean -fd"
    with runtime.container(image, config) as box:
        result = box.exec_shell(reset, workdir=image.workdir)
        if result.exit_code != 0:
            raise IsolationError(
                f"could not reset to base_commit for the solver view: {result.stderr or result.stdout}"
            )
        tar = box.get_archive(image.workdir)

    extract_tar(tar, dest)
    view_root = dest / Path(image.workdir).name
    shutil.rmtree(view_root / ".git", ignore_errors=True)  # no git history -> hidden tests unrecoverable.
    strip_hidden_tests(view_root, bundle)  # actively remove every file that defines a graded test.
    assert_hidden_absent(view_root, bundle)  # then PROVE none remains (defense in depth).
    return view_root


def strip_hidden_tests(view_root: Path, bundle: Bundle) -> None:
    """Remove every file in the view that defines a graded test (both buckets)."""
    for path in _graded_test_files(view_root, bundle, aggressive=True):
        try:
            path.unlink()
        except FileNotFoundError:
            continue
        except OSError as exc:  # a graded test we cannot remove would leak -> fail loud.
            raise IsolationError(f"could not strip hidden test file {path}: {exc}") from exc


def assert_hidden_absent(view_root: Path, bundle: Bundle) -> None:
    """Invariant #1: the solver view must not define any graded test (pass2pass or fail2pass)."""
    offenders = sorted(
        f"{path.relative_to(view_root).as_posix()}" for path in _graded_test_files(view_root, bundle, aggressive=False)
    )
    if offenders:
        detail = "\n  ".join(offenders)
        raise IsolationError(f"invariant #1 violated - hidden tests reachable by the solver:\n  {detail}")


def _graded_test_files(view_root: Path, bundle: Bundle, *, aggressive: bool) -> set[Path]:
    """Files in the view that define a graded test (both buckets).

    Precise-first: if a graded id's declared file actually defines its symbol, that file is the location
    (no tree scan -> no over-match on a generic function name shared across files). Otherwise we fall
    back to scanning the tree's TEST files (never arbitrary source). For a SPECIFIC locator (a class
    chain, or a bare path-less Go name) the
    fallback is always safe. For a lone pytest function name WITH a declared path the symbol is ambiguous,
    so the fallback fires only when `aggressive` (stripping): over-removing an unrelated same-named test
    file is safe, and it closes the relocated-function leak; the proof (`aggressive=False`) stays precise
    so a stripped declared file cannot trigger a spurious IsolationError on an unrelated namesake.
    """
    hits: set[Path] = set()
    tree: list[tuple[Path, str]] | None = None  # lazily built; only the fallback path needs it
    for nodeid in (*bundle.spec.buckets.fail2pass, *bundle.spec.buckets.pass2pass):
        idents, paths = _parse_target(nodeid)
        existing = [view_root / rel for rel in paths if (view_root / rel).is_file()]
        if not idents:  # mocha "file | describe::title" / bare file id -> strip the named file(s).
            hits.update(existing)
            continue
        primary = idents[0]
        declared = {path for path in existing if _defines(_safe_read(path), primary)}
        if declared:  # the graded test is in its declared file -> precise, no tree scan.
            hits.update(declared)
            continue
        specific = len(idents) >= 2 or not paths  # class chain or bare path-less name (unambiguous)
        if specific or aggressive:
            if tree is None:
                tree = list(_read_test_files(view_root))
            hits.update(path for path, text in tree if all(_defines(text, name) for name in idents))
    return hits


def _parse_target(nodeid: str) -> tuple[list[str], list[str]]:
    """(qualname identifier segments, file paths mentioned).

    'tests/x.py::Cls::test_a[p]' -> (['Cls', 'test_a'], ['tests/x.py'])  # class chain
    'tests/x.py::test_a'         -> (['test_a'], ['tests/x.py'])         # module-level function
    'TestBatchEvaluate'          -> (['TestBatchEvaluate'], [])          # bare Go/func name
    'a.js | desc b.js::it title' -> ([], ['a.js', 'b.js'])              # mocha: no symbol -> by file
    """
    paths = _PATH_RE.findall(nodeid)
    segments = nodeid.split("::")
    candidates = segments[1:] if len(segments) > 1 else segments
    idents = [token for seg in candidates if _IDENT_RE.fullmatch(token := seg.split("[")[0].strip())]
    return idents, paths


def _safe_read(path: Path) -> str:
    try:
        if path.stat().st_size > _MAX_TEST_FILE_BYTES:
            return ""
        return path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return ""


def _read_test_files(view_root: Path) -> Iterator[tuple[Path, str]]:
    for path in view_root.rglob("*"):
        rel = path.relative_to(view_root).as_posix()
        if not path.is_file() or not _is_test_file(rel):
            continue
        try:
            if path.stat().st_size > _MAX_TEST_FILE_BYTES:
                continue
            yield path, path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue


def _is_test_file(rel: str) -> bool:
    parts = rel.split("/")
    if any(part in _TEST_DIRS for part in parts[:-1]):
        return True
    return _TEST_NAME_RE.search(rel) is not None


def _defines(text: str, name: str) -> bool:
    """True if `text` defines a class/function named `name` (Python/JS/Go `class`/`def`/`func`)."""
    return re.search(_DEFINES_TMPL.format(name=re.escape(name)), text) is not None
