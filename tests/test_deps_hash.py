"""_deps_hash: an order-independent fingerprint of the resolved deps.lock, separate from config_hash."""

from __future__ import annotations

from pathlib import Path

from taskbundle.harness.run import _deps_hash


def test_deps_hash_is_order_independent(tmp_path: Path) -> None:
    a = tmp_path / "a.lock"
    b = tmp_path / "b.lock"
    a.write_text("numpy==2.1.3\npytest==8.4.1\n", encoding="utf-8")
    b.write_text("pytest==8.4.1\n\nnumpy==2.1.3\n", encoding="utf-8")  # reordered + blank line
    assert _deps_hash(a) == _deps_hash(b)  # same resolved set -> same hash


def test_deps_hash_differs_on_drift(tmp_path: Path) -> None:
    a = tmp_path / "a.lock"
    b = tmp_path / "b.lock"
    a.write_text("numpy==2.1.3\n", encoding="utf-8")
    b.write_text("numpy==2.2.0\n", encoding="utf-8")  # a different version = drift
    assert _deps_hash(a) != _deps_hash(b)


def test_deps_hash_empty_when_absent(tmp_path: Path) -> None:
    assert _deps_hash(tmp_path / "missing.lock") == ""  # no deps captured -> empty, not a crash
