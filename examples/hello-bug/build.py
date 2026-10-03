"""Build the shared example image and synchronize repair and synthesis revisions.

Run once to (re)generate the example bundle:  python examples/hello-bug/build.py
The captured commit, rather than a precomputed value, is written to both bundles.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
IMAGE = "taskbundle-hello-bug:1"
BUNDLE_NAMES = ("hello-bug", "hello-bug-synthesis")


def _run(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(args, capture_output=True, text=True)


def synchronize_bundles(base_commit: str) -> tuple[Path, ...]:
    """Validate both fixed targets before updating their shared image/revision."""
    if re.fullmatch(r"[0-9a-f]{40}", base_commit) is None:
        raise ValueError("image must report one full 40-character Git commit")
    examples = HERE.parent.resolve(strict=True)
    if examples.name != "examples" or HERE.resolve(strict=True) != examples / "hello-bug":
        raise ValueError("build helper must target examples/hello-bug")
    updates: list[tuple[Path, str]] = []
    for name in BUNDLE_NAMES:
        path = examples / name / "task.json"
        if path.resolve(strict=True) != path:
            raise ValueError(f"refusing redirected bundle target: {path}")
        task = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(task, dict) or task.get("id") != name:
            raise ValueError(f"unexpected bundle identity in {path}")
        image = task.get("image")
        if not isinstance(image, dict) or image.get("kind") != "prebuilt" or image.get("workdir") != "/app":
            raise ValueError(f"expected the shared prebuilt /app image in {path}")
        task["base_commit"] = base_commit
        image["ref"] = IMAGE
        image.pop("digest", None)
        updates.append((path, json.dumps(task, indent=2) + "\n"))
    for path, content in updates:
        path.write_text(content, encoding="utf-8")
    return tuple(path for path, _ in updates)


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

    try:
        paths = synchronize_bundles(base_commit)
    except (OSError, ValueError) as exc:
        sys.stderr.write(f"could not synchronize example bundles: {exc}\n")
        return 1
    print(f"built {IMAGE}; base_commit={base_commit}; wrote {', '.join(str(path) for path in paths)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
