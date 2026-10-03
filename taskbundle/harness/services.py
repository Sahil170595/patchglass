"""Shared harness plumbing: per-bundle data dir, store, artifact dirs, run ids, command logging."""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from taskbundle import constants
from taskbundle.db.store import Store


def data_dir(bundle_root: Path) -> Path:
    """Per-bundle state lives under <bundle>/.taskbundle (gitignored)."""
    return bundle_root / constants.DEFAULT_DATA_DIR


def open_store(bundle_root: Path) -> Store:
    return Store(data_dir(bundle_root) / constants.DB_FILENAME)


@contextmanager
def command_log(
    store_dir: Path, cmd_type: str, args: dict[str, object], *, bundle_id: str | None = None
) -> Iterator[tuple[Store, str]]:
    """Record a `commands` row for one CLI invocation; finish it ok/error around the body.

    The single place command logging lives — every CLI command routes through this so each invocation
    is auditable + queryable by id: information is stored every time a CLI command is invoked.
    Yields (store, command_id) so the body can write task/run rows tied to the same command_id.
    """
    store = open_store(store_dir)
    command_id = new_id("cmd")
    store.record_command(command_id, cmd_type, bundle_id, args)
    try:
        yield store, command_id
        store.finish_command(command_id, "ok")
    except BaseException as exc:
        store.finish_command(command_id, "error", str(exc))
        raise
    finally:
        store.close()


def run_artifacts_dir(bundle_root: Path, run_id: str) -> Path:
    path = data_dir(bundle_root) / "runs" / run_id
    path.mkdir(parents=True, exist_ok=True)
    return path


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:10]}"
