"""Module-level named constants. No magic numbers in leaf code — each value has a rationale."""

from __future__ import annotations

from pathlib import Path
from typing import Final

# --- Bundle / schema -------------------------------------------------------------------------
SCHEMA_VERSION: Final = 1  # task.json schema version; bump on breaking change, migrate on load.
JSON_INDENT: Final = 2  # human-readable indent for all JSON artifacts (run.json, task.json write-back).
DEFAULT_PLATFORM: Final = "linux/amd64"  # SWE-bench Pro images are amd64; pin to avoid QEMU drift.
DEFAULT_WORKDIR: Final = "/app"  # default repository location inside task images; overridable.

# --- SWE-bench Pro integration ---------------------------------------------------------------
SWEBENCH_PRO_IMAGE_PREFIX: Final = "jefzda/sweap-images"  # per-instance images: "{prefix}:{tag}".
SWEBENCH_PRO_DATASET: Final = "ScaleAI/SWE-bench_Pro"  # HuggingFace dataset id (public set).
HF_DATASETS_SERVER: Final = "https://datasets-server.huggingface.co"  # auth-free REST API.

# --- Test execution / parsing ----------------------------------------------------------------
# Sentinel markers wrap the test command's output so parsing is decoupled from execution
# (pattern is near-universal across SWE-bench-style harnesses).
TEST_OUTPUT_START: Final = ">>>>> Start Test Output"
TEST_OUTPUT_END: Final = ">>>>> End Test Output"

# --- Resource limits (defaults; per-task overridable via task.json `limits`) ------------------
DEFAULT_CPUS: Final = 2.0  # conservative per-task default; grading limits are configurable.
DEFAULT_MEM_MB: Final = 4096  # most repo test suites fit; OOM surfaces as a typed outcome.
DEFAULT_PIDS_LIMIT: Final = 512  # fork-bomb guard; verified-enforced via `task doctor`.
DEFAULT_GRADE_WALL_S: Final = 1800  # 30 min; matches the classic SWE-bench per-instance timeout.
DEFAULT_SOLVER_WALL_S: Final = 900  # 15 min cap on a single solver attempt.

# --- Patch application -----------------------------------------------------------------------
# Tried in order until one applies cleanly (robustness pattern from GSO / SWE-bench).
PATCH_APPLY_STRATEGIES: Final = (
    ("git", "apply", "--verbose"),
    ("git", "apply", "--verbose", "--3way"),
    ("git", "apply", "--verbose", "--ignore-space-change", "--ignore-whitespace"),
    ("git", "apply", "--verbose", "--reject"),
    ("patch", "--batch", "--fuzz=5", "-p1"),
)

# --- Determinism env (pinned inside every grading container) ----------------------------------
DETERMINISM_ENV: Final = {
    "TZ": "UTC",  # date/time-sensitive tests must not vary by host timezone.
    "LC_ALL": "C",  # locale-dependent sorting/formatting must be stable.
    "PYTHONHASHSEED": "0",  # dict/set ordering must be reproducible.
    "PYTHONDONTWRITEBYTECODE": "1",  # avoid stray .pyc artifacts in captured diffs.
}

# --- Reliability budgets ---------------------------------------------------------------------
MAX_CONTAINER_RETRIES: Final = 2  # infra failures (hang/OOM) are retried, not graded as failures.
FLAKY_CHECK_RUNS: Final = 3  # run baseline N times to detect flaky tests (matches the paper).

# --- Isolation (explicit runtime settings; see SECURITY.md for limits) -----------------------
CAP_DROP_ALL: Final = ["ALL"]  # drop every Linux capability for untrusted execution.
NO_NEW_PRIVILEGES: Final = "no-new-privileges:true"  # block setuid privilege escalation.
# Bundled Moby seccomp snapshot; apply explicitly instead of relying on daemon defaults.
SECCOMP_PROFILE_PATH: Final = Path(__file__).resolve().parent.parent / "seccomp" / "default.json"
NONROOT_USER: Final = "65534:65534"  # nobody:nogroup — runs the untrusted command-solver command non-root.
CONTAINER_IDLE_CMD: Final = ["tail", "-f", "/dev/null"]  # keep a container alive for exec/copy.
EXEC_KILL_GRACE_S: Final = 5  # extra wait after wall-clock before SIGKILL on a hung exec.

# --- Storage / artifacts ---------------------------------------------------------------------
DEFAULT_DATA_DIR: Final = ".taskbundle"  # under the bundle: db + artifacts + workspaces.
DB_FILENAME: Final = "taskbundle.db"
