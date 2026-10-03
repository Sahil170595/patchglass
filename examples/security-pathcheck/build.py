"""Build the synthetic security-pathcheck image and write task.json with the captured base_commit.

Run once to (re)generate the example bundle:  python examples/security-pathcheck/build.py
The commit is deterministic (fixed identity + dates in the Dockerfile), so base_commit is stable.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
IMAGE = "taskbundle-security-pathcheck:1"


def _run(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(args, capture_output=True, text=True)


def main() -> int:
    build = _run("docker", "build", "-t", IMAGE, str(HERE))
    if build.returncode != 0:
        sys.stderr.write(build.stdout + build.stderr)
        return 1

    rev = _run("docker", "run", "--rm", IMAGE, "git", "-C", "/app", "rev-parse", "HEAD")
    if rev.returncode != 0:
        sys.stderr.write(rev.stdout + rev.stderr)
        return 1
    base_commit = rev.stdout.strip()

    task = {
        "schema_version": 1,
        "id": "security-pathcheck",
        "repo": "local://security-pathcheck",
        "base_commit": base_commit,
        "language": "python",
        "domain": "security",
        "image": {"kind": "prebuilt", "ref": IMAGE, "workdir": "/app"},
        "test": {
            "run_cmd": "python -m pytest {test_files} -rA -p no:cacheprovider",
            "parser": "pytest",
            "selected_test_files": ["tests/test_traversal.py", "tests/test_allows.py"],
            "hidden_source": "bundle-files",
        },
        "buckets": {
            "pass2pass": ["tests/test_allows.py::test_allows_nested_file"],
            "fail2pass": ["tests/test_traversal.py::test_rejects_path_traversal"],
        },
        "provenance": {"source": "synthetic"},
    }
    (HERE / "task.json").write_text(json.dumps(task, indent=2) + "\n", encoding="utf-8")
    print(f"built {IMAGE}; base_commit={base_commit}; wrote {HERE / 'task.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
