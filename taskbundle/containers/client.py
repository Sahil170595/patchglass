"""Docker runtime with explicit capability, network, seccomp, and resource settings.
Enforcement depends on the daemon/kernel; these settings are not a hostile-code security guarantee.

Files cross the boundary as tar streams (put_archive/get_archive) — never bind mounts — which keeps
separates workspace transfer from host volume mounts.
"""

from __future__ import annotations

import io
import logging
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any

import docker

from taskbundle import constants
from taskbundle.containers.protocol import ExecResult, ResolvedImage
from taskbundle.errors import ContainerError, ImageError, IsolationError
from taskbundle.settings import Settings, get_settings

_log = logging.getLogger(__name__)

_NANO_PER_CPU = 1_000_000_000  # docker nano_cpus = cpus * 1e9.
_TIMEOUT_EXIT_CODE = 124  # conventional exit code for a wall-clock timeout (GNU timeout).
_PIDS_PROBE_VALUE = 37  # distinctive pids_limit for the doctor enforcement probe.
# Edge-case #16: we cross the boundary with tar streams only and NEVER bind-mount the host. These are
# the docker-SDK kwargs that would introduce a host mount; if any is ever present we refuse to run.
_HOST_MOUNT_KEYS = ("volumes", "binds", "mounts")


def _assert_no_host_mounts(kwargs: dict[str, Any]) -> None:
    """Invariant: no container bind-mounts the host (and so can never mount the docker socket)."""
    for key in _HOST_MOUNT_KEYS:
        if kwargs.get(key):
            raise IsolationError(
                f"refusing to run a container with a host mount ({key}={kwargs[key]!r}); "
                "the docker socket must never be mounted"
            )


@dataclass
class HardenedConfig:
    """Isolation knobs for a container. Use the factory presets, not the raw constructor."""

    network: str = "none"
    user: str | None = None
    cpus: float = constants.DEFAULT_CPUS
    mem_mb: int = constants.DEFAULT_MEM_MB
    pids: int = constants.DEFAULT_PIDS_LIMIT

    @classmethod
    def for_grading(cls, *, cpus: float, mem_mb: int, pids: int) -> HardenedConfig:
        """Grading needs a writable rootfs (git apply / test artifacts) but stays network-isolated."""
        return cls(network="none", user=None, cpus=cpus, mem_mb=mem_mb, pids=pids)

    @classmethod
    def for_command_solver(cls, *, cpus: float, mem_mb: int, pids: int, network: str = "none") -> HardenedConfig:
        """Untrusted solver command that must EDIT the repo: writable rootfs, network-isolated by default.

        The task's `solver_network` knob may opt into `bridge` (e.g. a command that installs tooling);
        it stays an explicit, per-task choice — the default is `none`.
        """
        return cls(network=network, user=None, cpus=cpus, mem_mb=mem_mb, pids=pids)


class Container:
    """A running container handle: exec commands, stream files in/out. Lifecycle owned by DockerRuntime."""

    def __init__(self, raw: Any) -> None:
        self._raw = raw

    @property
    def id(self) -> str:
        return str(self._raw.id)

    def exec(
        self,
        command: list[str],
        *,
        workdir: str | None = None,
        timeout_s: int = constants.DEFAULT_GRADE_WALL_S,
        user: str | None = None,
    ) -> ExecResult:
        """Run a command, capturing stdout/stderr separately. On wall-clock timeout, kill the container.

        `user` runs this exec as a specific uid[:gid] (e.g. drop the untrusted command to non-root while
        the trusted setup steps stay root).
        """
        start = time.monotonic()
        box: dict[str, Any] = {}
        exec_kwargs: dict[str, Any] = {"workdir": workdir, "demux": True}
        if user is not None:
            exec_kwargs["user"] = user

        def _work() -> None:
            try:
                code, (out, err) = self._raw.exec_run(command, **exec_kwargs)
                box["code"], box["out"], box["err"] = code, out or b"", err or b""
            except Exception as exc:  # docker API boundary — surface as a typed error below.
                box["exc"] = exc

        thread = threading.Thread(target=_work, daemon=True)
        thread.start()
        thread.join(timeout_s)
        elapsed_ms = int((time.monotonic() - start) * 1000)

        if thread.is_alive():
            self._kill()
            thread.join(constants.EXEC_KILL_GRACE_S)
            return ExecResult(
                "", f"wall-clock timeout after {timeout_s}s", _TIMEOUT_EXIT_CODE, elapsed_ms, timed_out=True
            )
        if "exc" in box:
            raise ContainerError(f"exec failed for {command!r}: {box['exc']}")
        return ExecResult(
            stdout=_decode(box["out"]),
            stderr=_decode(box["err"]),
            exit_code=int(box["code"]),
            duration_ms=elapsed_ms,
            oom_killed=self._oom_killed(),
        )

    def exec_shell(
        self,
        script: str,
        *,
        workdir: str | None = None,
        timeout_s: int = constants.DEFAULT_GRADE_WALL_S,
        shell: str = "/bin/sh",
        user: str | None = None,
    ) -> ExecResult:
        """Run a shell snippet (multi-command grading steps) via `<shell> -c`; `user` overrides the uid."""
        return self.exec([shell, "-c", script], workdir=workdir, timeout_s=timeout_s, user=user)

    def put_archive(self, dest_dir: str, tar_bytes: bytes) -> None:
        """Extract a tar stream into `dest_dir` (which must already exist) inside the container."""
        try:
            if not self._raw.put_archive(dest_dir, tar_bytes):
                raise ContainerError(f"put_archive to {dest_dir} returned False")
        except Exception as exc:
            raise ContainerError(f"put_archive to {dest_dir} failed: {exc}") from exc

    def get_archive(self, src_path: str) -> bytes:
        """Return a tar stream of `src_path` from inside the container."""
        try:
            bits, _stat = self._raw.get_archive(src_path)
            return b"".join(bits)
        except Exception as exc:
            raise ContainerError(f"get_archive of {src_path} failed: {exc}") from exc

    def _oom_killed(self) -> bool:
        try:
            self._raw.reload()
            return bool(self._raw.attrs["State"]["OOMKilled"])
        except Exception as exc:  # best-effort; absence of the flag is not itself an error.
            _log.debug("OOM-kill check failed: %s", exc)
            return False

    def _kill(self) -> None:
        try:
            self._raw.kill()
        except Exception as exc:  # already-dead container raises; nothing to do.
            _log.debug("container kill was a no-op: %s", exc)


class DockerRuntime:
    """Creates hardened containers and runs commands in them. Implements the Runtime seam."""

    def __init__(self, settings: Settings | None = None) -> None:
        self._settings = settings or get_settings()
        try:
            self._client = (
                docker.DockerClient(base_url=self._settings.docker_host)
                if self._settings.docker_host
                else docker.from_env()
            )
            self._client.ping()
        except Exception as exc:
            raise ContainerError(
                "cannot reach the Docker daemon — is Docker Desktop running? "
                f"(set TASKBUNDLE_DOCKER_HOST to override). Underlying error: {exc}"
            ) from exc

    def _create_kwargs(self, image: ResolvedImage, config: HardenedConfig) -> dict[str, Any]:
        security_opt = [constants.NO_NEW_PRIVILEGES]
        try:  # fail loud: never silently run a container unconfined because the profile is missing.
            security_opt.append("seccomp=" + constants.SECCOMP_PROFILE_PATH.read_text(encoding="utf-8"))
        except OSError as exc:
            raise ContainerError(
                f"seccomp profile unreadable at {constants.SECCOMP_PROFILE_PATH} ({exc}); refusing to run a "
                "container without it (Docker Desktop/WSL2 would default to unconfined). Restore/reinstall it."
            ) from exc
        mem = f"{config.mem_mb}m"
        kwargs: dict[str, Any] = {
            "image": image.ref,
            # Override the image's ENTRYPOINT (Scale's images set `/bin/bash`) so the keep-alive
            # process runs directly; command=[] clears any baked-in CMD. We exec into it afterward.
            "entrypoint": constants.CONTAINER_IDLE_CMD,
            "command": [],
            "detach": True,
            "network_mode": config.network,
            "cap_drop": constants.CAP_DROP_ALL,
            "security_opt": security_opt,
            "pids_limit": config.pids,
            "nano_cpus": int(config.cpus * _NANO_PER_CPU),
            "mem_limit": mem,
            "memswap_limit": mem,  # == mem_limit => no swap (best-effort on WSL2).
            "environment": dict(constants.DETERMINISM_ENV),
            "working_dir": image.workdir,
            "platform": image.platform,
        }
        if config.user:
            kwargs["user"] = config.user
        return kwargs

    @contextmanager
    def container(self, image: ResolvedImage, config: HardenedConfig) -> Iterator[Container]:
        """Create + start a hardened container; force-remove it on exit (no leaks)."""
        kwargs = self._create_kwargs(image, config)
        _assert_no_host_mounts(kwargs)  # defense-in-depth: never expose the host / docker socket.
        try:
            raw = self._client.containers.create(**kwargs)
        except Exception as exc:
            raise ContainerError(f"could not create container from {image.ref}: {exc}") from exc
        try:
            raw.start()
            yield Container(raw)
        finally:
            try:
                raw.remove(force=True)
            except Exception as exc:  # reaping is best-effort; must not mask the real result.
                _log.warning("could not remove container %s: %s", raw.id, exc)

    def run(self, image: ResolvedImage, command: list[str], *, network: str, wall_clock_s: int) -> ExecResult:
        """One-shot: run `command` in a hardened grading-style container and return the result."""
        config = HardenedConfig.for_grading(
            cpus=constants.DEFAULT_CPUS, mem_mb=constants.DEFAULT_MEM_MB, pids=constants.DEFAULT_PIDS_LIMIT
        )
        config.network = network
        with self.container(image, config) as box:
            return box.exec(command, timeout_s=wall_clock_s)

    def resolve_image(self, ref: str, platform: str) -> tuple[str, str]:
        """Obtain an image (local first, else pull); return (digest-pinned ref, sha256 digest)."""
        try:
            img = self._client.images.get(ref)
        except Exception as exc:  # not present locally — pull it from the registry.
            _log.debug("image %s not local (%s); pulling", ref, exc)
            try:
                img = self._client.images.pull(ref, platform=platform)
            except Exception as pull_exc:
                raise ImageError(f"could not obtain image {ref} ({platform}): {pull_exc}") from pull_exc
        repo_digests = img.attrs.get("RepoDigests") or []
        if repo_digests:
            pinned = str(repo_digests[0])  # "repo@sha256:..."
            digest = pinned.split("@", 1)[1] if "@" in pinned else str(img.id)
            return pinned, digest
        return ref, str(img.id)  # locally-built image without a registry digest.

    def image_present(self, ref: str) -> bool:
        """True if the image already exists locally (avoids rebuilding a content-addressed image)."""
        try:
            self._client.images.get(ref)
            return True
        except Exception:
            return False

    def build_image(self, dockerfile: str, tag: str, platform: str) -> str:
        """Build an image from an inline Dockerfile (no host context). Returns the image id."""
        try:
            image, _logs = self._client.images.build(
                fileobj=io.BytesIO(dockerfile.encode("utf-8")), tag=tag, platform=platform, rm=True, pull=False
            )
        except Exception as exc:
            raise ImageError(f"could not build image {tag}: {exc}") from exc
        return str(image.id)

    def put_archive(self, container_id: str, dest_dir: str, tar_bytes: bytes) -> None:
        Container(self._client.containers.get(container_id)).put_archive(dest_dir, tar_bytes)

    def get_archive(self, container_id: str, src_path: str) -> bytes:
        return Container(self._client.containers.get(container_id)).get_archive(src_path)

    def probe_pids_limit(self, probe_image: str = "alpine:3.20") -> bool | None:
        """Prove pids_limit is actually enforced: set a distinctive limit, read the cgroup back.

        Returns True (enforced), False (silently ignored — the WSL2 risk), or None (undeterminable).
        """
        try:
            ref, digest = self.resolve_image(probe_image, constants.DEFAULT_PLATFORM)
            image = ResolvedImage(ref=ref, digest=digest, platform=constants.DEFAULT_PLATFORM, workdir="/")
            config = HardenedConfig.for_grading(
                cpus=constants.DEFAULT_CPUS, mem_mb=constants.DEFAULT_MEM_MB, pids=_PIDS_PROBE_VALUE
            )
            with self.container(image, config) as box:
                read = "cat /sys/fs/cgroup/pids.max 2>/dev/null || cat /sys/fs/cgroup/pids/pids.max 2>/dev/null"
                value = box.exec(["sh", "-c", read]).stdout.strip()
        except Exception as exc:
            _log.debug("pids-limit probe failed: %s", exc)
            return None
        return value == str(_PIDS_PROBE_VALUE) if value else None

    def host_info(self) -> dict[str, str]:
        """Daemon facts used by `task doctor` (OS / arch / version)."""
        info = self._client.info()
        return {
            "os": str(info.get("OSType", "")),
            "arch": str(info.get("Architecture", "")),
            "server": str(info.get("ServerVersion", "")),
        }


def _decode(data: bytes) -> str:
    return data.decode("utf-8", errors="replace")
