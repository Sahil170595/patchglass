"""Run deterministic host tests without Docker, downloads, or provider calls."""

from __future__ import annotations

import os
import socket
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# These fixtures launch real containers. Pytest includes transitive fixture names.
_DOCKER_FIXTURES = frozenset({"runtime", "image", "_docker", "_env", "_graded"})


class OfflineOnly:
    def pytest_collection_modifyitems(self, items: list[pytest.Item]) -> None:
        for item in items:
            if _DOCKER_FIXTURES.intersection(getattr(item, "fixturenames", ())):
                item.add_marker(pytest.mark.skip(reason="offline profile: real Docker execution not verified"))


def _forbid(*args: object, **kwargs: object) -> None:
    raise AssertionError("offline profile forbids real Docker initialization and network connections")


def main() -> int:
    from taskbundle.containers.client import DockerRuntime

    os.environ["PYTEST_DISABLE_PLUGIN_AUTOLOAD"] = "1"
    sys.dont_write_bytecode = True
    # Fail rather than quietly contacting a daemon/provider if a new test bypasses the fixture selection.
    with (
        patch.object(DockerRuntime, "__init__", _forbid),
        patch.object(socket.socket, "connect", _forbid),
        patch.object(socket.socket, "connect_ex", _forbid),
        patch.object(socket, "create_connection", _forbid),
    ):
        return int(
            pytest.main(
                [str(ROOT / "tests"), "-ra", "-p", "no:cacheprovider", "--basetemp", str(ROOT / ".release-test-tmp")],
                plugins=[OfflineOnly()],
            )
        )


if __name__ == "__main__":
    raise SystemExit(main())
