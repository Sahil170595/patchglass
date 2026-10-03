# Patchglass

**A container-based repair and test-synthesis harness with inspectable grading evidence.**

Patchglass packages a repository at a commit, gives a solver a restricted workspace,
and evaluates its patch in a fresh container. It also evaluates generated regression
tests against a buggy baseline and a reference code fix. The Python harness, CLI,
Docker runtime, benchmark importer, parsers, SQLite store, suite runner, and example
bundles are included here. This is the full source system, not a browser simulation.

[Architecture](docs/ARCHITECTURE.md) | [Reproduction](docs/REPRODUCTION.md) |
[Security and support limits](SECURITY.md) | [Licenses](THIRD_PARTY_NOTICES.md)

The [portfolio work page](https://chimeraforge.vercel.app/work) provides broader context.
The separate [browser edition, PR 57](https://github.com/Sahil170595/Banterblogs/pull/57)
is a draft pending integration and QA. It illustrates grading semantics; it does not
execute this Docker harness or a live model and is not evidence of sandbox security.

## Architecture at a glance

```text
task.json + description + optional reference patches + hidden test files
                       |
                 schema / image provider
                       |
        baseline validation -> extracted solver view
                       |                 |
                       |          solver -> patch
                       |                 |
                       +------> fresh grade container
                                         |
                          parser -> verdict -> run.json / SQLite
```

- **Repair:** validate pass-to-pass and fail-to-pass buckets on the baseline;
  apply a solver patch, restore hidden tests, parse individual outcomes, and compute
  a conjunctive after-state verdict. Optional baseline grading adds transition diagnostics.
- **Test synthesis:** discover candidate test IDs, reject recognized non-test edits,
  and execute the candidate before and after the reference code patch. Changed-line
  coverage is diagnostic, not an acceptance requirement.
- **Solvers:** reference/no-op controls; containerized shell commands; host-side
  single-shot model adapters with search/replace edits, lexical localization, and
  applicability retries. Test-writing model support is currently Ollama-only.
- **Evidence:** image/patch/config/dependency hashes, selected context paths, per-test
  outcomes, command status, raw run artifacts, and grouped suite reports.

The distribution, Python namespace, environment prefix, and installed command retain
their compatibility names: `taskbundle`, `TASKBUNDLE_`, and `task`.

## Quickstart

Python 3.11+, Git, and Docker with Linux containers are required for the full lifecycle.
The default target is `linux/amd64`; Windows users need a working Docker Desktop/WSL2
daemon. Images and packages must be obtained before offline container runs are possible.

```sh
git clone https://github.com/Sahil170595/patchglass.git
cd patchglass
uv sync --locked --extra dev
uv run task --help
uv run python -B tools/test_offline.py
```

`uv` is optional: a virtual environment with `pip install -e '.[dev]'` also works,
but does not enforce `uv.lock`. The offline profile blocks real network connections
and Docker initialization and explicitly skips container-dependent tests.

### Repair lifecycle (Docker)

```sh
uv run python examples/hello-bug/build.py
uv run task doctor
uv run task init -b examples/hello-bug
uv run task validate -b examples/hello-bug
uv run task validate -b examples/hello-bug --patched
uv run task run -b examples/hello-bug --solver golden --baseline --fail-on-unresolved
uv run task run -b examples/hello-bug --solver noop --baseline
uv run task ls -b examples/hello-bug
```

The reference patch should resolve the authored normalization bug; the no-op should
not. These are reproducible control experiments to run locally, not published model
performance results. `run` returns zero for a completed but unresolved run unless
`--fail-on-unresolved` is set. Use the printed run ID with `task log <run_id> -b ...`
and `task diff <run_id> -b ...` to inspect the result.

### Generated-test lifecycle

The synthesis bundle reuses the image built above.

```sh
uv run task init -b examples/hello-bug-synthesis
uv run task validate -b examples/hello-bug-synthesis
uv run task run -b examples/hello-bug-synthesis --solver synth-golden --fail-on-unresolved
uv run task run -b examples/hello-bug-synthesis --solver synth-noop
```

An already-installed local Ollama model can replace a control with
`--solver llm-synth:ollama/<installed-model>`. Repair uses
`--solver llm:ollama/<installed-model>`, optionally `--localize --max-attempts 2`.
No model, weights, provider credentials, captured responses, or external benchmark
dataset is distributed here. Remote repair adapters are opt-in and may incur charges;
no remote generation is needed for the quickstart controls or offline tests.

## Bundles, runners, and suites

Five original synthetic bundles cover normalization, annual interest, path traversal,
integer powers, and reproducing-test synthesis. They intentionally contain buggy
baseline code. Build scripts update their local `task.json` commit; Dockerfiles use
floating base tags, so they are examples, not immutable cross-machine environments.

`task new --help` scaffolds a custom bundle. The author supplies the commit, image,
build/test commands, buckets, and hidden tests. Python has a default scaffold runner;
other languages need explicit commands and parser selection. Eight built-in parser
formats cover pytest, unittest/Django, Go, Jest, Mocha, ansible, normalized benchmark
JSON, and sanitizer reproduction output. Parser support is not blanket support for
every version or plugin of those ecosystems.

```sh
uv run task suite run -b examples/hello-bug --solver golden --solver noop --workers 1
uv run task report --by language
```

For a useful synthesis comparison, run a separate suite with `synth-golden` and
`synth-noop`; the repair `golden` control emits a code fix, not a test candidate.
The `task import` adapter remains implemented, but downloading third-party tasks,
images, and run scripts is separate, networked work subject to their own licenses.

## What the checks establish

The offline suite exercises schema boundaries, verdict arithmetic, parsers,
import transformation with authored rows, SQLite reporting, localization,
reward-hack detection, workspace checks, and mocked model protocols. Docker tests
remain in `tests/` for operators with prepared images. See
[reproduction](docs/REPRODUCTION.md) for the distinction between unit evidence,
container evidence, and live-model evidence.

This initial public release has no fresh Docker lifecycle or live-provider
qualification. It makes no benchmark success-rate, universal determinism, hidden-test
confidentiality, vulnerability-remediation, or production multi-tenant security claim.
In particular, synthesis accepts baseline **non-pass**, not only assertion failure;
repair counts `XFAIL` as passing. These semantics require careful inspection of
per-test evidence. [Security and support limits](SECURITY.md) explains the trust boundary.

## License and release scope

First-party code, tests, docs, and the five synthetic bundles use the original
[MIT license](LICENSE). The unchanged bundled Moby seccomp profile is
Apache-2.0; its pinned provenance and full license are retained in
[THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md) and `licenses/`.
External benchmark fixtures and operational artifacts are not vendored. The
[release scope](docs/RELEASE_SCOPE.md) lists the included source surfaces and exclusions.
For reproducible bug reports, open an issue with a minimal synthetic bundle and
sanitized environment/version information, not credentials or raw private run artifacts.
