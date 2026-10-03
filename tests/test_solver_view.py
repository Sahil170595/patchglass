"""Recognized hidden-test stripping, using independently authored synthetic source strings.

Cases cover relocated classes, bare Go names, and Mocha path/title IDs. These finite checks
do not prove confidentiality for arbitrary programs, histories, or image contents.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from taskbundle.bundle.loader import Bundle
from taskbundle.bundle.schema import TaskSpec
from taskbundle.errors import IsolationError
from taskbundle.harness.solver_view import assert_hidden_absent, strip_hidden_tests


def _bundle(*, fail2pass: list[str] | None = None, pass2pass: list[str] | None = None) -> Bundle:
    spec = TaskSpec.model_validate(
        {
            "id": "x",
            "repo": "r",
            "base_commit": "a" * 40,
            "image": {"kind": "prebuilt", "ref": "x:1"},
            "test": {"run_cmd": "pytest {test_files}", "parser": "pytest", "selected_test_files": []},
            "buckets": {"pass2pass": pass2pass or [], "fail2pass": fail2pass or []},
        }
    )
    return Bundle(root=Path("."), spec=spec, description="", patch=None, solution_test=None)


def _write(root: Path, rel: str, text: str) -> Path:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


# --- existing contract: absence passes; a same-named method under a DIFFERENT class elsewhere is
#     not a false positive; a real same-file leak fires ---


def test_passes_when_hidden_file_absent(tmp_path: Path) -> None:
    _write(tmp_path, "tests/test_x.py", "def test_other():\n    pass\n")
    assert_hidden_absent(tmp_path, _bundle(fail2pass=["tests/test_x.py::test_hidden"]))  # no raise


def test_no_false_positive_on_same_name_other_file(tmp_path: Path) -> None:
    _write(tmp_path, "tests/test_other.py", "class Other:\n    def test_primary(self):\n        pass\n")
    assert_hidden_absent(tmp_path, _bundle(fail2pass=["tests/test_declared.py::TestPolicy::test_primary"]))


def test_fires_on_real_same_file_leak(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "tests/test_declared.py",
        "class TestPolicy:\n    def test_primary(self):\n        pass\n",
    )
    with pytest.raises(IsolationError, match="invariant #1"):
        assert_hidden_absent(tmp_path, _bundle(fail2pass=["tests/test_declared.py::TestPolicy::test_primary"]))


# --- relocated definitions and language-specific identifier shapes ---


def test_detects_and_strips_relocated_class(tmp_path: Path) -> None:
    # A declared path is not enough: the graded class lives in a sibling file.
    _write(
        tmp_path,
        "tests/test_declared.py",
        "class TestVisible:\n    def test_identity(self):\n        assert 3 == 3\n",
    )
    _write(
        tmp_path,
        "tests/test_actual.py",
        "class TestPolicy:\n"
        "    def test_primary(self):\n"
        "        assert calculate_total([4, 8]) == 12\n"
        "    def test_secondary(self, amount):\n"
        "        assert amount >= 0\n"
        "class TestStable:\n"
        "    def test_stable(self):\n"
        "        assert 5 == 5\n",
    )
    bundle = _bundle(
        fail2pass=[
            "tests/test_declared.py::TestPolicy::test_primary",
            "tests/test_declared.py::TestPolicy::test_secondary[12]",
        ],
        pass2pass=["tests/test_actual.py::TestStable::test_stable"],
    )
    with pytest.raises(IsolationError, match="invariant #1"):  # the relocated class is a real leak
        assert_hidden_absent(tmp_path, bundle)
    strip_hidden_tests(tmp_path, bundle)
    assert not (tmp_path / "tests" / "test_actual.py").exists()  # the file holding the graded tests is gone
    assert_hidden_absent(tmp_path, bundle)  # now provably clean
    blob = "\n".join(p.read_text(encoding="utf-8", errors="ignore") for p in tmp_path.rglob("*.py"))
    assert "TestPolicy" not in blob and "calculate_total" not in blob


def test_detects_and_strips_bare_go_funcs(tmp_path: Path) -> None:
    # Bare Go names have no pytest path/class delimiters.
    _write(
        tmp_path,
        "sample/sequence_test.go",
        "package sample\n"
        "func TestSequenceTotal(t *testing.T) {\n"
        '    if Total(4, 8) != 12 { t.Fatal("wrong total") }\n'
        "}\n"
        "func TestSequenceEmpty(t *testing.T) {\n"
        '    if Total() != 0 { t.Fatal("wrong empty total") }\n'
        "}\n",
    )
    bundle = _bundle(fail2pass=["TestSequenceTotal", "TestSequenceEmpty"])
    with pytest.raises(IsolationError, match="invariant #1"):  # was a no-op before the fix
        assert_hidden_absent(tmp_path, bundle)
    strip_hidden_tests(tmp_path, bundle)
    assert not (tmp_path / "sample" / "sequence_test.go").exists()
    assert_hidden_absent(tmp_path, bundle)
    blob = "\n".join(p.read_text(encoding="utf-8", errors="ignore") for p in tmp_path.rglob("*.go"))
    assert "wrong empty total" not in blob


def test_strips_mocha_test_file_by_path(tmp_path: Path) -> None:
    # A Mocha identifier can carry two paths plus a title, without a Python/Go symbol.
    _write(
        tmp_path,
        "test/sequence/total.js",
        "describe('Sequence', () => {\n  it('totals two items', () => {});\n});\n",
    )
    nodeid = "test/sequence.js | Test sequence test/sequence/total.js::Sequence totals two items"
    bundle = _bundle(fail2pass=[nodeid])
    with pytest.raises(IsolationError, match="invariant #1"):
        assert_hidden_absent(tmp_path, bundle)
    strip_hidden_tests(tmp_path, bundle)
    assert not (tmp_path / "test" / "sequence" / "total.js").exists()
    assert_hidden_absent(tmp_path, bundle)


def test_pass2pass_is_also_hidden(tmp_path: Path) -> None:
    # Pass-to-pass IDs are hidden too.
    _write(tmp_path, "tests/test_p.py", "class TestGraded:\n    def test_a(self):\n        pass\n")
    bundle = _bundle(pass2pass=["tests/test_p.py::TestGraded::test_a"])
    with pytest.raises(IsolationError, match="invariant #1"):
        assert_hidden_absent(tmp_path, bundle)
    strip_hidden_tests(tmp_path, bundle)
    assert_hidden_absent(tmp_path, bundle)


def test_strips_relocated_module_level_function(tmp_path: Path) -> None:
    # The single-segment case: a bare graded function whose nodeid names test_a.py, but which physically
    # lives in a sibling test_b.py. Stripping is aggressive enough to catch it; the proof then holds.
    _write(tmp_path, "tests/test_a.py", "def test_other():\n    pass\n")  # declared file, lacks the graded fn
    _write(tmp_path, "tests/test_b.py", "def test_widget():\n    assert compute() == 42  # graded\n")
    bundle = _bundle(fail2pass=["tests/test_a.py::test_widget"])
    strip_hidden_tests(tmp_path, bundle)
    assert not (tmp_path / "tests" / "test_b.py").exists()  # the relocated graded fn is stripped
    assert_hidden_absent(tmp_path, bundle)
    blob = "\n".join(p.read_text(encoding="utf-8", errors="ignore") for p in tmp_path.rglob("*.py"))
    assert "compute() == 42" not in blob  # the graded assertion is unreadable
    assert (tmp_path / "tests" / "test_a.py").exists()  # an unrelated declared file with no namesake stays


def test_net_new_function_does_not_over_strip(tmp_path: Path) -> None:
    # A genuinely net-new graded fn (absent everywhere at base) must not strip an unrelated namesake.
    _write(tmp_path, "tests/test_a.py", "def test_other():\n    pass\n")
    _write(tmp_path, "src/helpers.py", "def test_widget():\n    return 1  # NOT a graded test, just a fn\n")
    bundle = _bundle(fail2pass=["tests/test_a.py::test_widget"])
    strip_hidden_tests(tmp_path, bundle)
    assert (tmp_path / "src" / "helpers.py").exists()  # non-test file with a namesake fn is untouched
