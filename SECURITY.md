# Security and support limits

Patchglass is a local research harness, not an audited hostile-code execution service.
Run it on a disposable worker or VM without valuable data, secrets, or host services.
Docker access is privileged. Do not expose the CLI, Docker daemon, database, or optional
dashboard as a public multi-tenant service.

## Trust boundary

Task authors, images, build/setup commands, reference patches, grading scripts, and
parser implementations are trusted inputs. They can execute arbitrary code during
image preparation or grading. Image building has a different trust boundary from the
restricted runtime. Review inputs and their licenses before running them.

Runtime containers drop capabilities, set no-new-privileges, apply the bundled
seccomp snapshot, reject host-mount kwargs, and request CPU/memory/PID limits.
The root filesystem is writable. Trusted setup and grading may run as root; the
command solver's untrusted command is invoked as UID/GID 65534. `none` networking
is the default; explicit `bridge` settings allow unfiltered egress. Requested limits
are not proof of enforcement on every daemon/kernel. `doctor` probes PID limits,
but an inconclusive probe is a warning rather than a hard failure.

The command solver currently uses default resource/time budgets rather than all
per-bundle solver limit overrides. Review `solvers/command.py` before relying on
custom budgets. The included seccomp policy is from 2023; review current kernel and
upstream hardening requirements independently.

## Hidden tests and host model context

Host-side model solvers receive an extracted workspace with `.git` and recognized
hidden-test content removed. Content detection uses names, AST traversal, textual
patterns, and bounded reads; it is not semantic information-flow analysis. Unusual
languages, aliases, binaries, unreadable files, or indirect disclosures can defeat it.

The command solver runs in the environment image after resetting its checkout to
the base commit, not in the sanitized host snapshot. Git objects, image layers, and
other image paths are not comprehensively scrubbed. It must not be treated as a
hidden-test confidentiality boundary for adversarial agents. Use task images that
contain no hidden answers or sensitive material; do not rely on a clean checkout
alone to conceal history.

Host-side model generation is outside the container network boundary. Remote adapters
transmit the selected source context to a provider when explicitly selected. The
release confines search/replace target paths to the extracted workspace (including
resolved symlink checks), but the host process still has ordinary filesystem access.
The workspace must not be concurrently mutated by an adversarial host process.

## Grading is evidence, not a proof

`block` reverts recognized touched test infrastructure and outcome-override files.
Its path/regex detectors are incomplete and may flag legitimate changes; `warn` is
advisory and can permit forged outcomes. A code patch can still alter behavior outside
the test surface or spoof output in ways these checks do not recognize.

Repair treats `XFAIL` as passing. Synthesis recognizes a candidate as reproducing when
its baseline status is anything other than `PASSED` and its patched status is `PASSED`.
That includes baseline collection errors, skips, and missing outcomes, not only failing
assertions. Inspect pre/post statuses and output before interpreting the verdict.
Changed-line coverage does not gate acceptance or establish a test's causal validity.
The sanitizer parser checks build-success and absence of recognized crash markers;
it does not prove that a PoC ran, that the exit was healthy, or that a vulnerability is fixed.

## Artifact handling and support

Runs can persist source snapshots, prompts/model answers, patches, stdout/stderr,
commands, dependency records, and local paths under `.taskbundle/`. These can contain
secrets or copyrighted material supplied by the operator. Keep them private; review
and redact before sharing. `.gitignore` is not a redaction tool. Avoid public Gradio
share links (`--share`) for run histories.

Use the GitHub repository's private vulnerability reporting facility when available,
or report a minimal sanitized reproducer through an issue without exploit credentials
or sensitive artifacts. There is no guaranteed response-time SLA or production support.
