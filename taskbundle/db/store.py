"""SQLite store. WAL mode + a process-level lock serialize writes (safe under batch fan-out).

Large stdout/stderr blobs live in the artifacts dir and are referenced by `log_ref`; the DB stays
small and the logs stay greppable.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path

_SCHEMA_SQL = Path(__file__).with_name("schema.sql")


def _utcnow() -> str:
    return datetime.now(UTC).isoformat()


@dataclass
class RunRecord:
    """A single run's row (most columns optional; filled as the lifecycle progresses)."""

    run_id: str
    command_id: str | None = None
    suite_id: str | None = None
    task_id: str | None = None
    solver: str | None = None
    model: str | None = None
    image_ref: str | None = None
    image_digest: str | None = None
    platform: str | None = None
    base_commit: str | None = None
    solver_patch_sha: str | None = None
    limits_json: str | None = None
    verdict: str | None = None
    resolved: bool | None = None
    patch_exists: bool | None = None
    patch_applied: bool | None = None
    patch_is_none: bool | None = None
    oom_killed: bool | None = None
    timed_out: bool | None = None
    cost_json: str | None = None


@dataclass
class ResultRow:
    """A single per-test outcome row."""

    test_id: str
    bucket: str
    outcome: str
    duration_ms: int | None = None
    log_ref: str | None = None


@dataclass
class RateRow:
    """One row of a resolve-rate aggregate (the `task report` output)."""

    group: str
    total: int
    resolved: int
    rate: float


@dataclass
class CommandRow:
    """Lightweight view of a command for listing."""

    id: str
    type: str
    bundle_id: str | None
    status: str
    started_at: str
    finished_at: str | None
    error: str | None
    args: dict[str, object] = field(default_factory=dict)


class Store:
    """Thin, typed wrapper over a SQLite database file."""

    def __init__(self, db_path: str | Path) -> None:
        self._path = Path(db_path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(self._path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._conn.execute("PRAGMA busy_timeout=5000")  # wait, don't error, under concurrent batch writes.
        self._init_schema()

    def _init_schema(self) -> None:
        with self._lock:
            self._conn.executescript(_SCHEMA_SQL.read_text(encoding="utf-8"))
            self._ensure_column("tasks", "domain", "TEXT")  # additive migration for DBs created pre-domain.
            self._conn.commit()

    def _ensure_column(self, table: str, column: str, decl: str) -> None:
        existing = {r["name"] for r in self._conn.execute(f"PRAGMA table_info({table})").fetchall()}
        if column not in existing:
            self._conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {decl}")

    def close(self) -> None:
        self._conn.close()

    # --- commands ----------------------------------------------------------------------------
    def record_command(self, command_id: str, type_: str, bundle_id: str | None, args: dict[str, object]) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO commands (id, type, bundle_id, args_json, status, started_at) "
                "VALUES (?, ?, ?, ?, 'started', ?)",
                (command_id, type_, bundle_id, json.dumps(args), _utcnow()),
            )
            self._conn.commit()

    def finish_command(self, command_id: str, status: str, error: str | None = None) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE commands SET status = ?, finished_at = ?, error = ? WHERE id = ?",
                (status, _utcnow(), error, command_id),
            )
            self._conn.commit()

    def get_command(self, command_id: str) -> CommandRow | None:
        row = self._conn.execute("SELECT * FROM commands WHERE id = ?", (command_id,)).fetchone()
        if row is None:
            return None
        return CommandRow(
            id=row["id"],
            type=row["type"],
            bundle_id=row["bundle_id"],
            status=row["status"],
            started_at=row["started_at"],
            finished_at=row["finished_at"],
            error=row["error"],
            args=json.loads(row["args_json"]),
        )

    def recent_commands(self, limit: int = 20) -> list[CommandRow]:
        rows = self._conn.execute("SELECT * FROM commands ORDER BY started_at DESC LIMIT ?", (limit,)).fetchall()
        return [
            CommandRow(
                id=r["id"],
                type=r["type"],
                bundle_id=r["bundle_id"],
                status=r["status"],
                started_at=r["started_at"],
                finished_at=r["finished_at"],
                error=r["error"],
                args=json.loads(r["args_json"]),
            )
            for r in rows
        ]

    # --- tasks / runs / results --------------------------------------------------------------
    def upsert_task(
        self,
        task_id: str,
        bench_source: str,
        *,
        instance_id: str | None = None,
        repo: str | None = None,
        language: str | None = None,
        domain: str | None = None,
        image_ref: str | None = None,
        image_digest: str | None = None,
    ) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO tasks (task_id, bench_source, instance_id, repo, language, domain, image_ref, "
                "image_digest) VALUES (?, ?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(task_id) DO UPDATE SET bench_source=excluded.bench_source, repo=excluded.repo, "
                "language=excluded.language, domain=excluded.domain, image_ref=excluded.image_ref, "
                "image_digest=excluded.image_digest",
                (task_id, bench_source, instance_id, repo, language, domain, image_ref, image_digest),
            )
            self._conn.commit()

    def record_run(self, run: RunRecord) -> None:
        data = asdict(run)
        cols = ", ".join([*data.keys(), "created_at"])
        placeholders = ", ".join(["?"] * (len(data) + 1))
        values = [*(_as_db(v) for v in data.values()), _utcnow()]
        with self._lock:
            self._conn.execute(f"INSERT INTO runs ({cols}) VALUES ({placeholders})", values)
            self._conn.commit()

    def record_test_results(self, run_id: str, results: list[ResultRow]) -> None:
        with self._lock:
            self._conn.executemany(
                "INSERT INTO test_results (run_id, test_id, bucket, outcome, duration_ms, log_ref) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                [(run_id, r.test_id, r.bucket, r.outcome, r.duration_ms, r.log_ref) for r in results],
            )
            self._conn.commit()

    def create_suite(self, suite_id: str, name: str, spec_json: str) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO suites (suite_id, name, spec_json, created_at) VALUES (?, ?, ?, ?)",
                (suite_id, name, spec_json, _utcnow()),
            )
            self._conn.commit()

    _GROUP_COLUMNS = {
        "solver": "runs.solver",
        "model": "runs.model",
        "task": "runs.task_id",
        "language": "tasks.language",
        "domain": "tasks.domain",
        "repo": "tasks.repo",
        "bench": "tasks.bench_source",
    }

    def resolve_rates(self, *, suite_id: str | None = None, group_by: str = "solver") -> list[RateRow]:
        """Aggregate resolve-rate (Pass@1) over runs, grouped by one dimension. The `task report` backend."""
        column = self._GROUP_COLUMNS.get(group_by)
        if column is None:
            raise ValueError(f"unknown --by {group_by!r}; choose from {sorted(self._GROUP_COLUMNS)}")
        where = "WHERE runs.suite_id = ?" if suite_id else ""
        params: tuple[str, ...] = (suite_id,) if suite_id else ()
        rows = self._conn.execute(
            f"SELECT {column} AS grp, COUNT(*) AS total, SUM(COALESCE(resolved, 0)) AS resolved "
            f"FROM runs LEFT JOIN tasks ON runs.task_id = tasks.task_id {where} "
            "GROUP BY grp ORDER BY grp",
            params,
        ).fetchall()
        out: list[RateRow] = []
        for row in rows:
            total = int(row["total"])
            resolved = int(row["resolved"] or 0)
            out.append(
                RateRow(
                    group=str(row["grp"] if row["grp"] is not None else "(none)"),
                    total=total,
                    resolved=resolved,
                    rate=round(resolved / total, 3) if total else 0.0,
                )
            )
        return out

    def resolved_cells(self) -> set[tuple[str, str]]:
        """(task_id, solver) pairs that already have a resolved run — for idempotent suite --resume."""
        rows = self._conn.execute(
            "SELECT DISTINCT task_id, solver FROM runs "
            "WHERE resolved = 1 AND task_id IS NOT NULL AND solver IS NOT NULL"
        ).fetchall()
        return {(str(row["task_id"]), str(row["solver"])) for row in rows}

    def get_run(self, run_id: str) -> dict[str, object] | None:
        row = self._conn.execute("SELECT * FROM runs WHERE run_id = ?", (run_id,)).fetchone()
        if row is None:
            return None
        results = self._conn.execute(
            "SELECT test_id, bucket, outcome, duration_ms, log_ref FROM test_results WHERE run_id = ?",
            (run_id,),
        ).fetchall()
        run = dict(row)
        run["test_results"] = [dict(r) for r in results]
        return run


def _as_db(value: object) -> object:
    """Map Python bools to 0/1 for SQLite; pass everything else through."""
    return int(value) if isinstance(value, bool) else value
