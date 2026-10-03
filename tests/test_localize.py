"""Localization: pick the right files for the solver prompt instead of a flat dump (offline)."""

from __future__ import annotations

from pathlib import Path

from taskbundle.solvers.localize import issue_identifiers, select_files, visible_test_imports


def _repo(tmp_path: Path) -> Path:
    (tmp_path / "widget").mkdir()
    (tmp_path / "widget" / "core.py").write_text(
        "class LabelPolicy:\n    pass\n\n\ndef normalize(text):\n    return text.strip()\n", encoding="utf-8"
    )
    (tmp_path / "widget" / "helpers.py").write_text("def unrelated():\n    return 42\n", encoding="utf-8")
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_core.py").write_text(
        "from widget.core import normalize\n\n\ndef test_n():\n    assert normalize(' A ') == 'a'\n", encoding="utf-8"
    )
    (tmp_path / "unrelated").mkdir()
    (tmp_path / "unrelated" / "big.py").write_text("# noise\n" + "x = 1\n" * 500, encoding="utf-8")
    return tmp_path


def test_issue_identifiers_extracts_code_like_names() -> None:
    ids = issue_identifiers("The `normalize` function and the LabelPolicy class are broken; fix lowercasing.")
    assert "normalize" in ids and "LabelPolicy" in ids
    assert "function" not in ids  # plain prose word dropped (not code-like, not backticked)


def test_visible_test_imports_resolve_to_source_files(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    targets = visible_test_imports(root, ["tests/test_core.py"])
    assert "widget/core.py" in targets


def test_select_ranks_relevant_file_in_under_tight_budget(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    sel = select_files(
        root,
        "normalize should lowercase the label; see LabelPolicy",
        visible_tests=["tests/test_core.py"],
        max_total_bytes=200,
    )
    assert "widget/core.py" in sel  # defines the named symbols AND imported by the visible test
    assert "unrelated/big.py" not in sel  # squeezed out by the tight budget
    assert "tests/test_core.py" not in sel  # never feed the visible tests into the repair context


def test_select_excludes_test_looking_files(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    sel = select_files(root, "normalize", visible_tests=[], max_total_bytes=10_000)
    assert not any("test" in path for path in sel)  # test-looking files filtered from repair context


def test_select_is_non_empty_even_without_signal(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    sel = select_files(root, "completely unrelated query xyzzy", visible_tests=[], max_total_bytes=10_000)
    assert sel  # degrades to a budget-filled selection rather than returning nothing
