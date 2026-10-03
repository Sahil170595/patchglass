"""Path helper for serving files from a fixed base directory."""

import os


def safe_join(base_dir: str, user_path: str) -> str | None:
    """Join `user_path` under `base_dir`, returning None if it escapes the base.

    Security: a `user_path` such as '../../etc/passwd' must NOT be allowed to escape base_dir.
    """
    candidate = os.path.normpath(os.path.join(base_dir, user_path))
    return candidate
