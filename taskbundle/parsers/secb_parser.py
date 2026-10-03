"""SEC-bench sanitizer parser: a crash-repro is the "test", inverted into pass/fail grading.

SEC-bench tasks have no unit-test suite. The grading signal is a sanitizer reproduction: the baseline
(unpatched) build crashes a sanitizer (ASAN SEGV / UBSan / leak) on a PoC input; a correct fix makes
the same PoC run cleanly. So the single graded id `repro` PASSES iff the build succeeded AND the run
showed NO sanitizer crash marker, and FAILS otherwise (crash still reproduces, or the build broke).

This is the fail2pass invariant for a security task: baseline `repro` FAILS (crash), patched `repro`
PASSES (clean) — same shape as a fail2pass unit test, just sourced from a sanitizer instead of pytest.
"""

from __future__ import annotations

import re
from typing import Final

from taskbundle.parsers.base import RunOutput, TestStatus, normalize

NAME: Final = "secb_sanitizer"

# The single conceptual test id this parser grades (the crash reproduction).
_REPRO_ID: Final = "repro"

# `secb build` prints exactly these on success / failure (src: /usr/local/bin/secb in the SEC-bench image).
# Build success is a precondition for a meaningful clean repro — a missing/failed build is NOT a fix.
_BUILD_OK_MARKER: Final = "BUILD COMPLETED SUCCESSFULLY!"
_BUILD_FAIL_MARKER: Final = "BUILD FAILED!"

# Sanitizer crash markers. Any match means the PoC still triggered the vulnerability -> repro FAILED.
# - "AddressSanitizer" / "LeakSanitizer" / generic "SUMMARY: <X>Sanitizer": ASAN/LSAN/UBSan summaries.
# - "SEGV on": the ASAN "SEGV on unknown address" deadly-signal line.
# - "ERROR: <X>Sanitizer": the ASAN/LSAN error banner.
# - "runtime error:": UBSan's per-finding line (a sanitizer crash even without an ASAN summary).
_CRASH_MARKERS: Final = re.compile(
    r"AddressSanitizer"
    r"|LeakSanitizer"
    r"|ThreadSanitizer"
    r"|MemorySanitizer"
    r"|UndefinedBehaviorSanitizer"
    r"|SUMMARY:\s*\S*Sanitizer"
    r"|ERROR:\s*\S*Sanitizer"
    r"|SEGV on"
    r"|runtime error:",
    re.IGNORECASE,
)


def _build_succeeded(text: str) -> bool:
    """True iff the build's success marker is present and its failure marker is not."""
    return _BUILD_OK_MARKER in text and _BUILD_FAIL_MARKER not in text


def _repro_status(text: str) -> TestStatus:
    """PASSED iff the build succeeded AND no sanitizer crash marker is present; else FAILED."""
    if not _build_succeeded(text):
        return TestStatus.FAILED  # build broke (or never ran) — cannot count as a clean fix.
    if _CRASH_MARKERS.search(text):
        return TestStatus.FAILED  # PoC still crashes the sanitizer — the bug is not fixed.
    return TestStatus.PASSED  # no recognized crash marker; does not prove PoC execution or remediation.


class SecbSanitizerParser:
    """Grade a SEC-bench crash reproduction: `repro` passes iff the build is clean and no sanitizer fired."""

    name = NAME

    def parse(self, output: RunOutput, expected_ids: list[str]) -> dict[str, TestStatus]:
        # Combine stdout+stderr: the harness captures the marker-delimited stdout, but a sanitizer may
        # write to either stream depending on the runner, so we scan both before deciding.
        text = normalize(output.stdout + "\n" + output.stderr)
        status = _repro_status(text)
        return {tid: (status if tid == _REPRO_ID else TestStatus.MISSING) for tid in expected_ids}


parser: Final = SecbSanitizerParser()
