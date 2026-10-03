"""doctor critical-gate logic (offline): proven pids non-enforcement is a hard gate (edge #17)."""

from __future__ import annotations

from taskbundle.harness.doctor import Check, critical_ok


def test_critical_ok_passes_when_only_warnings() -> None:
    checks = [
        Check("docker daemon reachable", True, "ok", critical=True),
        Check("pids-limit actually enforced", True, "could not probe", critical=False),  # undeterminable -> warn
        Check("seccomp profile bundled", False, "missing", critical=False),  # non-critical warning
    ]
    assert critical_ok(checks) is True


def test_critical_ok_fails_on_proven_pids_non_enforcement() -> None:
    checks = [
        Check("docker daemon reachable", True, "ok", critical=True),
        Check("pids-limit actually enforced", False, "SILENTLY IGNORED", critical=True),  # proven False -> hard gate
    ]
    assert critical_ok(checks) is False


def test_critical_ok_fails_on_docker_unreachable() -> None:
    assert critical_ok([Check("docker daemon reachable", False, "down", critical=True)]) is False
