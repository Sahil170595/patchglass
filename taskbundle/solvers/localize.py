"""Localization: select the RIGHT files for the solver prompt instead of a flat truncated dump.

The naive solver fails on real repos because the file that needs editing is never in the 200 KB
flat slice. This ranks files by three cheap, stdlib-only signals
and fills the budget with the top files' full bodies:

  1. imported by a VISIBLE test  -> almost certainly the code under test (strongest signal),
  2. defines a symbol named in the issue (`def`/`class <id>`),
  3. lexical overlap with the issue text.

Test-looking files are filtered out of the repair context (Agentless `filter_out_test_files`) so the
solver keys on source, not on test internals — and never tries to "fix" the task by editing a test.
"""

from __future__ import annotations

import ast
import re
from collections.abc import Iterator
from pathlib import Path
from typing import Final

_CODE_SUFFIXES: Final = (".py", ".js", ".ts", ".go", ".java", ".rb", ".rs", ".c", ".cpp", ".h")
_SKIP_DIRS: Final = {".git", "node_modules", "__pycache__", ".venv", "dist", "build"}
_MAX_FILE_BYTES: Final = 40_000  # per-file cap (mirrors the flat selector); larger files are skipped.

# Ranking weights (relative). A file imported by a visible test is the single strongest signal that
# it is the code under test; defining a named symbol is next; raw lexical overlap is the weak tiebreak.
_W_TEST_IMPORT: Final = 50.0
_W_DEFINES_ID: Final = 12.0
_W_MENTIONS_ID: Final = 2.0
_W_LEXICAL: Final = 8.0

_MIN_IDENT_LEN: Final = 3  # ignore 1-2 char tokens (too noisy to key on).
_IDENT_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]{2,}")
_BACKTICK_RE = re.compile(r"`([^`]+)`")
# Prose words that would pollute lexical scoring; identifiers are already filtered to code-like tokens.
_STOPWORDS: Final = frozenset(
    {
        "the",
        "and",
        "for",
        "with",
        "that",
        "this",
        "from",
        "when",
        "not",
        "but",
        "are",
        "was",
        "fix",
        "bug",
        "issue",
        "should",
        "would",
        "could",
        "test",
        "tests",
        "class",
        "def",
        "function",
        "method",
        "return",
        "value",
        "code",
        "file",
        "files",
        "line",
        "lines",
        "case",
        "use",
        "used",
        "using",
        "set",
        "get",
        "add",
        "remove",
        "call",
        "called",
        "into",
        "via",
        "all",
        "any",
        "one",
        "two",
        "new",
        "old",
    }
)


def _iter_code_files(root: Path) -> Iterator[Path]:
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.suffix not in _CODE_SUFFIXES:
            continue
        if any(part in _SKIP_DIRS for part in path.relative_to(root).parts):
            continue
        yield path


def _read(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None


def _is_camel(token: str) -> bool:
    return any(c.isupper() for c in token[1:]) and any(c.islower() for c in token)


def _looks_like_test(rel: str) -> bool:
    """Agentless-style filter: a file that is (or lives under) a test, which the solver must not edit."""
    parts = rel.split("/")
    if any(part in ("test", "tests") for part in parts[:-1]):
        return True
    name = parts[-1]
    return name.startswith("test_") or name.endswith(("_test.py", ".test.js", ".test.ts", "_test.go"))


def issue_identifiers(description: str) -> set[str]:
    """Code-like names referenced in the issue: backtick-quoted, CamelCase, or snake_case (not prose)."""
    candidates: set[str] = set()
    for quoted in _BACKTICK_RE.findall(description):
        candidates.update(_IDENT_RE.findall(quoted))  # `obj.method` -> {obj, method}
    for token in _IDENT_RE.findall(description):
        if "_" in token or _is_camel(token):
            candidates.add(token)
    return {c for c in candidates if len(c) >= _MIN_IDENT_LEN and c.lower() not in _STOPWORDS}


def _module_to_paths(module: str, names: list[str]) -> list[str]:
    """Repo-relative path candidates a dotted import could resolve to (module file, package, submodule)."""
    base = module.replace(".", "/")
    cands = [f"{base}.py", f"{base}/__init__.py"]
    cands += [f"{base}/{n}.py" for n in names]  # `from pkg import submod` -> pkg/submod.py
    return cands


def _code_index(root: Path) -> set[str]:
    """All code-file paths in the view, view-root-relative (e.g. 'app/pkg/utils.py')."""
    return {p.relative_to(root).as_posix() for p in _iter_code_files(root)}


def _resolve(candidate: str, index: set[str]) -> str | None:
    """Map a repo-root-relative path to the actual view path, tolerating a workdir prefix (e.g. 'app/').

    SWE-bench Pro nests the repo under the container workdir, so `selected_test_files` and import targets
    are repo-relative but the view paths carry the prefix — match by path suffix, shallowest wins.
    """
    if candidate in index:
        return candidate
    matches = [p for p in index if p.endswith("/" + candidate)]
    return min(matches, key=len) if matches else None


def _imports_for(root: Path, visible_tests: list[str], index: set[str]) -> set[str]:
    targets: set[str] = set()
    for rel in visible_tests:
        resolved = _resolve(rel, index)
        src = _read(root / resolved) if resolved else None
        if src is None:
            continue
        try:
            tree = ast.parse(src)
        except SyntaxError:
            continue  # a non-Python or unparseable visible test contributes no import signal.
        candidates: list[str] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                candidates += _module_to_paths_for(node)
            elif isinstance(node, ast.ImportFrom) and node.module:
                candidates += _module_to_paths(node.module, [a.name for a in node.names])
        targets.update(p for c in candidates if (p := _resolve(c, index)) is not None)
    return targets


def _module_to_paths_for(node: ast.Import) -> list[str]:
    out: list[str] = []
    for alias in node.names:
        out += _module_to_paths(alias.name, [])
    return out


def visible_test_imports(root: Path, visible_tests: list[str]) -> set[str]:
    """Source files imported by the visible test files (the code under test), as view-relative paths."""
    return _imports_for(root, visible_tests, _code_index(root))


def _score(rel: str, content: str, identifiers: set[str], targets: set[str], issue_tokens: set[str]) -> float:
    score = _W_TEST_IMPORT if rel in targets else 0.0
    for ident in identifiers:
        if re.search(rf"\b(?:def|class)\s+{re.escape(ident)}\b", content):
            score += _W_DEFINES_ID
        elif ident in content:
            score += _W_MENTIONS_ID
    if issue_tokens:
        file_tokens = {t.lower() for t in _IDENT_RE.findall(content)}
        coverage = len(issue_tokens & file_tokens) / len(issue_tokens)
        score += _W_LEXICAL * coverage
    return score


def select_files(root: Path, description: str, *, visible_tests: list[str], max_total_bytes: int) -> dict[str, str]:
    """Rank code files by relevance to the issue and return the top files (full body) within the budget."""
    identifiers = issue_identifiers(description)
    index = _code_index(root)
    targets = _imports_for(root, visible_tests, index)
    visible = {p for v in visible_tests if (p := _resolve(v, index)) is not None}
    issue_tokens = {t.lower() for t in _IDENT_RE.findall(description) if t.lower() not in _STOPWORDS}

    scored: list[tuple[float, str, str]] = []
    for path in _iter_code_files(root):
        rel = path.relative_to(root).as_posix()
        if rel in visible or _looks_like_test(rel):
            continue  # never feed/edit tests in the repair context.
        content = _read(path)
        if content is None or len(content) > _MAX_FILE_BYTES:
            continue
        scored.append((_score(rel, content, identifiers, targets, issue_tokens), rel, content))

    scored.sort(key=lambda item: (-item[0], item[1]))  # highest score first; path as a stable tiebreak.
    selected: dict[str, str] = {}
    total = 0
    for _score_value, rel, content in scored:
        if total + len(content) > max_total_bytes:
            continue  # keep filling with smaller lower-ranked files rather than stopping.
        selected[rel] = content
        total += len(content)
    return selected
