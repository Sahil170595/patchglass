"""Host-side model edits must remain inside the extracted solver workspace."""

from pathlib import Path

import pytest

from taskbundle.solvers.llm import _apply_edits, _Edit


@pytest.mark.parametrize("absolute", [False, True])
def test_model_edit_cannot_write_outside_workspace(tmp_path: Path, absolute: bool) -> None:
    root = tmp_path / "view"
    root.mkdir()
    outside = tmp_path / "outside.py"
    outside.write_text("old\n", encoding="utf-8")
    path = str(outside) if absolute else "../outside.py"
    result = _apply_edits(root, [_Edit(path=path, search="old", replace="new")])
    assert outside.read_text(encoding="utf-8") == "old\n"
    assert result.patch == "" and result.applied == 0


def test_model_edit_cannot_follow_symlink_outside_workspace(tmp_path: Path) -> None:
    root = tmp_path / "view"
    root.mkdir()
    outside = tmp_path / "outside.py"
    outside.write_text("old\n", encoding="utf-8")
    link = root / "linked.py"
    try:
        link.symlink_to(outside)
    except OSError:
        pytest.skip("this host cannot create symlinks")
    result = _apply_edits(root, [_Edit(path="linked.py", search="old", replace="new")])
    assert outside.read_text(encoding="utf-8") == "old\n"
    assert result.patch == "" and result.applied == 0


def test_model_edit_still_updates_nested_workspace_file(tmp_path: Path) -> None:
    target = tmp_path / "pkg" / "module.py"
    target.parent.mkdir()
    target.write_text("old\n", encoding="utf-8")
    result = _apply_edits(tmp_path, [_Edit(path="pkg/module.py", search="old", replace="new")])
    assert target.read_text(encoding="utf-8") == "new\n"
    assert result.applied == 1 and "+++ b/pkg/module.py" in result.patch
