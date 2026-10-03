"""Build the synthetic go-demo-pow image and write task.json with the captured base_commit.

Run once to (re)generate the example bundle:  python examples/go-demo-pow/build.py
Demonstrates the native `go` (gotest) parser on a real `go test` run. Stdlib-only -> grades offline.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
IMAGE = "taskbundle-go-demo-pow:1"


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
        "id": "go-demo-pow",
        "repo": "local://go-demo-pow",
        "base_commit": base_commit,
        "language": "go",
        "domain": "swe",
        "image": {"kind": "prebuilt", "ref": IMAGE, "workdir": "/app"},
        "test": {
            "run_cmd": "go test ./mathx/ -v",
            "parser": "go",
            "selected_test_files": ["mathx/pow_test.go"],
            "hidden_source": "bundle-files",
        },
        "buckets": {"pass2pass": [], "fail2pass": ["TestPow"]},
        "provenance": {"source": "go-demo"},
    }
    (HERE / "task.json").write_text(json.dumps(task, indent=2) + "\n", encoding="utf-8")
    print(f"built {IMAGE}; base_commit={base_commit}; wrote {HERE / 'task.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
