-- Storage schema with task and suite grouping for batch reporting.

CREATE TABLE IF NOT EXISTS commands (
    id           TEXT PRIMARY KEY,
    type         TEXT NOT NULL,           -- init | validate | run | import | doctor | ...
    bundle_id    TEXT,
    args_json    TEXT NOT NULL,
    status       TEXT NOT NULL,           -- started | ok | error
    started_at   TEXT NOT NULL,
    finished_at  TEXT,
    error        TEXT
);

CREATE TABLE IF NOT EXISTS tasks (
    task_id       TEXT PRIMARY KEY,
    bench_source  TEXT NOT NULL,
    instance_id   TEXT,
    repo          TEXT,
    language      TEXT,
    domain        TEXT,                    -- swe | finance | security | ... (for `task report --by domain`)
    image_ref     TEXT,
    image_digest  TEXT
);

CREATE TABLE IF NOT EXISTS suites (
    suite_id    TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    spec_json   TEXT,
    created_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS runs (
    run_id           TEXT PRIMARY KEY,
    command_id       TEXT REFERENCES commands(id),
    suite_id         TEXT REFERENCES suites(suite_id),
    task_id          TEXT REFERENCES tasks(task_id),
    solver           TEXT,
    model            TEXT,
    image_ref        TEXT,
    image_digest     TEXT,
    platform         TEXT,
    base_commit      TEXT,
    solver_patch_sha TEXT,
    limits_json      TEXT,
    verdict          TEXT,                -- resolved | not-resolved | error
    resolved         INTEGER,             -- 0/1 (derived: all fail2pass PASSED AND all pass2pass PASSED)
    patch_exists     INTEGER,
    patch_applied    INTEGER,
    patch_is_none    INTEGER,
    oom_killed       INTEGER,
    timed_out        INTEGER,
    cost_json        TEXT,                -- tokens/wall-clock/retries sidecar
    created_at       TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS test_results (
    run_id      TEXT NOT NULL REFERENCES runs(run_id),
    test_id     TEXT NOT NULL,
    bucket      TEXT NOT NULL,           -- pass2pass | fail2pass
    outcome     TEXT NOT NULL,           -- PASSED | FAILED | SKIPPED | ERROR | XFAIL | MISSING
    duration_ms INTEGER,
    log_ref     TEXT,
    PRIMARY KEY (run_id, test_id)
);

CREATE INDEX IF NOT EXISTS idx_runs_task ON runs(task_id);
CREATE INDEX IF NOT EXISTS idx_runs_suite ON runs(suite_id);
CREATE INDEX IF NOT EXISTS idx_results_run ON test_results(run_id);
