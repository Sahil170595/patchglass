"""CLI surface smoke — help/version work; unimplemented commands exit non-zero with a clear note."""

from __future__ import annotations

from typer.testing import CliRunner

from taskbundle import __version__
from taskbundle.cli import app

runner = CliRunner()


def test_help_lists_commands() -> None:
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    for cmd in ("init", "validate", "run", "log", "ls", "import", "doctor", "diff"):
        assert cmd in result.output


def test_version() -> None:
    result = runner.invoke(app, ["--version"])
    assert result.exit_code == 0
    assert __version__ in result.output


def test_missing_bundle_errors_actionably() -> None:
    result = runner.invoke(app, ["init", "--bundle", "no-such-dir-xyz"])
    assert result.exit_code == 1
    assert "error" in result.output.lower()  # typed BundleNotFoundError, not a traceback
