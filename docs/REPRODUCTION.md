# Reproduction and verification

## Offline host checks

Use an existing Python 3.11+ environment containing the project dependencies,
pytest, Ruff, Black, and mypy. Installation is optional when those are already present.
From the repository root:

```sh
python -B tools/test_offline.py
ruff check --no-cache taskbundle tests tools
black --check taskbundle tests tools
mypy --cache-dir .release-mypy taskbundle tests
python -B -m taskbundle --help
```

The offline runner disables third-party pytest plugin autoload, bytecode, and pytest's
cache provider. Test scratch data is confined to `.release-test-tmp/`. It skips tests
depending on live-container fixtures and blocks real socket connections and Docker
initialization. Mock HTTP protocol responses remain local authored test inputs.
Skipped container tests must not be reported as passed. A symlink boundary test may
also skip on Windows hosts without symlink creation privileges.

Coverage includes normalized verdicts and missing outcomes, digest write-back,
dependency hashing, importer mapping, tar traversal filtering, mount rejection,
timeout/cleanup behavior with doubles, SQLite aggregates, context localization,
test-infrastructure reversion selection, and model edit extraction/application.
These tests do not demonstrate actual kernel limit enforcement, image preparation,
container execution, model quality, remote API compatibility, or benchmark results.

Initial release check (2026-10-03): Python 3.13.1, pytest 9.0.3,
Pydantic 2.13.4, pydantic-settings 2.14.1, Docker SDK 7.1.0, Typer 0.27.1.
The offline profile passed 182 tests and skipped 26: 25 live-Docker cases and one
symlink case unavailable on the Windows host. Ruff 0.16.2 passed; Black 26.1.0
accepted 85 files; mypy 2.0.0 reported no issues in 83 source files. CLI help/version
and `uv lock --check --offline` passed. These checks used an existing environment,
not a newly installed locked environment. A wheel was not built because the local
build backend was unavailable; no dependency installation was performed.

## Container qualification (separate)

On a disposable Linux-container worker with space for base images, build
`examples/hello-bug/build.py` first and run `task doctor`. Initial builds contact image
registries and package repositories. The reference/no-op repair and synthesis commands
in the README provide controlled positive and negative cases without model calls.

```sh
python -B -m pytest tests/test_containers.py tests/test_grade_e2e.py tests/test_cli_e2e.py
python -B -m pytest tests/test_command_solver.py tests/test_reward_hack_block.py
python -B -m pytest tests/test_test_synthesis.py tests/test_synthesis_run.py tests/test_llm_synth.py
```

The final file's container test still mocks generation; it verifies the wiring from
test text to graded container result, not live inference. Inspect skip reasons: source
integration tests can skip when Docker or an image is absent. A green pytest exit with
skips is not a completed container qualification.

Finance, path-check, and Go bundles each have their own `build.py`. `hello-bug-synthesis`
shares hello-bug's image; hello-bug's build helper captures its actual Git commit and
updates both known bundle revisions together. It preserves their separate test settings
and clears old image digests. The checked-in revision describes the normalized LF source
tree; rebuilding always replaces it with the actual image revision. Host-side helper
tests mock Docker calls and do not qualify container execution. Floating base tags and
package repositories mean rebuilding images is not byte-identical reproduction.

## Inspect a run

Per-bundle state is under `.taskbundle/`; `task ls`, `task log`, and `task diff` expose
the indexed history. Read the corresponding `runs/<run_id>/run.json`, `solver.patch`,
solver metadata, and grader output. For synthesis, inspect the pre/post statuses and
changed-line coverage separately. For repair, confirm baseline validation, applied
patch status, hidden bucket outcomes, warnings, and infra failures before interpreting
the resolved flag.

For comparisons, record the Patchglass commit, Python and Docker versions, image ID
and portable registry digest where available, full `task.json`, runner version,
network posture, dependency capture, solver parameters, and preserved patch. Start
with a fresh suite store when changing inputs; resume is not a config-aware cache.

No historical run artifacts or precise model performance scores accompany this release.
The initial release qualification is offline-only. Fresh container and live-model
experiments must be reported with their own conditions and evidence, not inferred from
the included implementation or control-test expectations.
