"""Tar helpers for streaming files in/out of containers (put_archive/get_archive use tar)."""

from __future__ import annotations

import io
import tarfile
from pathlib import Path


def dir_to_tar(src: Path) -> bytes:
    """Pack a directory's contents (relative paths) into a tar stream."""
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as tar:
        for path in sorted(src.rglob("*")):
            tar.add(path, arcname=path.relative_to(src).as_posix())
    return buf.getvalue()


def file_to_tar(arcname: str, data: bytes) -> bytes:
    """Pack a single in-memory file into a tar stream (e.g. a patch to drop into the container)."""
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as tar:
        info = tarfile.TarInfo(name=arcname)
        info.size = len(data)
        tar.addfile(info, io.BytesIO(data))
    return buf.getvalue()


def extract_tar(tar_bytes: bytes, dest: Path) -> None:
    """Extract a tar stream into `dest` (data filter blocks path-traversal/absolute members)."""
    dest.mkdir(parents=True, exist_ok=True)
    with tarfile.open(fileobj=io.BytesIO(tar_bytes), mode="r") as tar:
        tar.extractall(dest, filter="data")


def read_member(tar_bytes: bytes, member_suffix: str) -> str | None:
    """Return the text of the first member whose name ends with `member_suffix`, or None."""
    with tarfile.open(fileobj=io.BytesIO(tar_bytes), mode="r") as tar:
        for member in tar.getmembers():
            if member.isfile() and member.name.endswith(member_suffix):
                handle = tar.extractfile(member)
                if handle is not None:
                    return handle.read().decode("utf-8", errors="replace")
    return None
