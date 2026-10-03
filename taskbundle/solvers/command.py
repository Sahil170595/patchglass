"""Command solver: run an arbitrary (untrusted) shell command inside a hardened container against the
base-commit repo, then capture the `git diff` it produced as the patch.

This is the untrusted-execution path: the command runs with all caps dropped, the bundled seccomp
profile, cpu/mem/pids limits, `--network none` by default, and as a NON-ROOT user — it can edit the repo
with no requested host mounts. The trusted harness resets/chmods the repo as root, then drops
to non-root to run the untrusted command (and capture its diff). The task's `solver_network` knob may opt
into `bridge` egress. A base-commit reset does not scrub Git objects or other image contents;
this path is not a hidden-test confidentiality guarantee (see SECURITY.md).
"""

from __future__ import annotations

import shlex

from taskbundle import constants
from taskbundle.containers.client import DockerRuntime, HardenedConfig
from taskbundle.containers.protocol import ResolvedImage
from taskbundle.errors import SolverError
from taskbundle.solvers.base import SolverResult, WorkspaceHandle

_ERR_TAIL = 800  # chars of a failed command's stderr/stdout to surface in the error (actionable, not a wall).


class CommandSolver:
    """Run a shell command in an isolated container and return the diff it produced."""

    name = "command"

    def __init__(
        self,
        runtime: DockerRuntime,
        image: ResolvedImage,
        base_commit: str,
        command: str,
        *,
        network: str = "none",
    ) -> None:
        self._runtime = runtime
        self._image = image
        self._base_commit = base_commit
        self._command = command
        self._network = network

    def solve(self, workspace: WorkspaceHandle, description: str) -> SolverResult:
        config = HardenedConfig.for_command_solver(
            cpus=constants.DEFAULT_CPUS,
            mem_mb=constants.DEFAULT_MEM_MB,
            pids=constants.DEFAULT_PIDS_LIMIT,
            network=self._network,
        )
        workdir = self._image.workdir
        base = shlex.quote(self._base_commit)
        user = constants.NONROOT_USER
        with self._runtime.container(self._image, config) as box:
            # Trusted setup runs as root: reset to base, then make the repo world-writable so the untrusted
            # command can edit it as a non-root user. We use chmod (an owner DAC op, allowed with no
            # capabilities) rather than chown, which would need CAP_CHOWN — and we drop ALL caps on purpose.
            setup = box.exec_shell(
                f"git reset --hard {base} && git checkout {base} -- . && git clean -fd && chmod -R o+rwX .",
                workdir=workdir,
            )
            if setup.exit_code != 0:
                raise SolverError(f"could not reset to base for the command solver: {setup.stderr or setup.stdout}")
            # Untrusted command + its diff run as NON-ROOT (no caps, no-new-privs, network-none, ephemeral).
            # The repo stays root-owned, so git is told the workdir is safe for this uid (no global config).
            safe = f"git -c safe.directory={shlex.quote(workdir)}"
            ran = box.exec_shell(self._command, workdir=workdir, timeout_s=constants.DEFAULT_SOLVER_WALL_S, user=user)
            if ran.exit_code != 0:  # a crashing command must fail loud, not silently return a stale/partial diff.
                detail = (ran.stderr or ran.stdout or "").strip()[:_ERR_TAIL]
                raise SolverError(f"command solver command exited {ran.exit_code}: {detail}")
            diff = box.exec_shell(f"{safe} diff", workdir=workdir, user=user)
        return SolverResult(patch=diff.stdout, meta={"solver": self.name, "command_exit": ran.exit_code})
