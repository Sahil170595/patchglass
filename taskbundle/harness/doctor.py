"""`task doctor`: preflight checks for a reproducible, isolated run on this host."""

from __future__ import annotations

from dataclasses import dataclass

from taskbundle import constants
from taskbundle.containers.client import DockerRuntime
from taskbundle.errors import TaskBundleError

_EXPECTED_OS = "linux"
_EXPECTED_ARCH = "x86_64"


@dataclass
class Check:
    name: str
    ok: bool
    detail: str
    critical: bool = False  # a failing critical check makes `task doctor` exit non-zero.


def run_doctor() -> list[Check]:
    """Return the list of preflight checks. `critical_ok()` tells the CLI whether to exit non-zero."""
    checks: list[Check] = []

    try:
        runtime = DockerRuntime()
    except TaskBundleError as exc:
        checks.append(Check("docker daemon reachable", False, str(exc), critical=True))
        return checks
    checks.append(Check("docker daemon reachable", True, "ok", critical=True))

    seccomp = constants.SECCOMP_PROFILE_PATH
    checks.append(
        Check(
            "seccomp profile bundled",
            seccomp.is_file(),
            f"{seccomp} (Windows/WSL2 defaults to unconfined without this)",
        )
    )

    info = runtime.host_info()
    checks.append(
        Check(
            f"platform is {_EXPECTED_OS}/{_EXPECTED_ARCH}",
            info["os"] == _EXPECTED_OS and info["arch"] == _EXPECTED_ARCH,
            f"daemon reports {info['os']}/{info['arch']} (server {info['server']}); "
            "non-amd64 hosts run images under emulation and may differ",
        )
    )

    enforced = runtime.probe_pids_limit()
    detail = {
        True: "cgroup reflects the limit",
        False: "SILENTLY IGNORED — a fork bomb in a task could exhaust the host (configure WSL2 cgroup v2)",
        None: "could not probe (pull alpine?)",
    }[enforced]
    # edge #17: a PROVEN-unenforced pids limit is a hard gate (a fork bomb would escape a phantom limit);
    # undeterminable (None) only warns — we don't fail a host just because the probe couldn't run.
    checks.append(Check("pids-limit actually enforced", enforced is not False, detail, critical=enforced is False))
    return checks


def critical_ok(checks: list[Check]) -> bool:
    """True iff every CRITICAL check passed (docker reachability + proven-enforced isolation limits)."""
    return all(c.ok for c in checks if c.critical)
