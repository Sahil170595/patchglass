# Architecture and grading semantics

## The full system

The architecture separates task content, execution, solver behavior, and verdict logic.
`bundle/schema.py` validates the bundle boundary. `containers/image.py` obtains either
a prebuilt image or a repository-at-commit image built from declared commands.
`containers/client.py` owns container lifecycle, tar transfer, execution limits, and
timeout cleanup. The language boundary is a declared runner and registered parser,
not a hard-coded Python evaluator.

`harness/validate.py` checks baseline bucket polarity, optional reference resolution,
repeated baseline stability, digest posture, and a no-op/gameability check. Empty
repair buckets are rejected during validation. Schema-valid scaffolds deliberately
remain incomplete until their author supplies real test buckets.

`harness/solver_view.py` resets the checkout, extracts files, removes recognized
graded tests and Git metadata, strips selected Python test definitions, and checks
recognized hidden-test identifiers. Visible tests remain available. This addresses
both obvious hidden files and definitions embedded in other files, but the
[security limits](../SECURITY.md) are essential: recognized absence is not universal
confidentiality, especially on the separate command-solver path.

`solvers/base.py` defines a workspace-to-patch protocol. The model repair adapter
requests search/replace blocks rather than trusting generated diff hunk arithmetic.
It classifies no blocks, unmatched context, no-op edits, and applied edits. Retry
feedback concerns applicability, not hidden-test outcomes. Localization ranks files
by visible Python imports, issue-mentioned definitions, and lexical overlap, then
fills a bounded context budget. This is a heuristic selector, not a trained retriever
or autonomous planning agent. Model responses are generated on the host.

`harness/grade.py` resets a fresh grading container, applies patches using ordered
fallback strategies, restores recognized test infrastructure, stages hidden tests,
runs the declared command, and collects raw/structured outputs. Missing expected
IDs remain `MISSING`; they are not silently dropped from a successful verdict.
Infrastructure timeouts/OOMs have bounded retries. Patch fallback and parser behavior
must still be inspected for unusual repositories and runner versions.

## Repair verdict

`reporting.compute_report` requires an applied, non-empty patch and passing after-state
outcomes for every declared fail-to-pass and pass-to-pass ID. This implementation
counts `PASSED` and `XFAIL` as passing. `FAILED`, `ERROR`, `SKIPPED`, and `MISSING`
are non-passing. Baseline-relative transition groups are diagnostics; the after-state
verdict does not itself establish that the original baseline was valid. Run `validate`
before comparing solvers. An empty schema-level bucket is not a meaningful benchmark.

The repair path's structural `block` policy reverts recognized test-infrastructure
edits, including newly added outcome hooks. Regex detectors add audit signals; they
are neither exhaustive malicious-code detection nor an independent secure grader.

## Test-synthesis verdict

The candidate is a test patch; the separate reference patch fixes the code. Recognized
non-test candidate edits are rejected to discourage self-fixing. Candidate IDs are
collected in a fresh baseline environment and run before and after the reference fix.
Discovery currently assumes pytest-style node IDs even when a custom collection command
is declared; repair's broader parser roster does not imply equally broad synthesis support.

The implemented acceptance condition is at least one candidate with
`pre != PASSED` and `post == PASSED`. A skip, collection error, missing status, or other
non-pass can satisfy its first half. It is weaker than requiring a baseline assertion
failure and does not prove the generated test reproduces the intended bug. Examine
all candidate statuses, not just the resolved flag. Coverage compares executed lines
with added/changed reference-patch lines; it is an auxiliary warning, not a gate.

## Evidence, storage, and batches

`harness/run.py` writes the solver patch, selected context file names, generation
metadata, available raw response, and grading report into a per-run artifact directory.
SQLite tracks commands, tasks, runs, suites, and normalized per-test outcomes. WAL and
per-store write locks support threaded suite execution; this is not a distributed queue.
`harness/suite.py` fans out bundle/solver cells and supports resuming previously resolved
task/solver pairs. Resume keys do not include a complete config/environment fingerprint;
use a fresh store when comparing changed bundles or configurations.

Reports group resolved runs by solver, model, task, language, domain, repository, or
benchmark label. The rate is over recorded runs, not a confidence interval or unbiased
benchmark estimate; retries, filtering, and resume behavior can affect its interpretation.

`config_hash` covers a selected subset of bundle inputs, not every limit, staging command,
or networking setting. `deps_hash` records the dependency capture the image exposes;
it is not a complete OS/package supply-chain lock. Preserve the full bundle, image,
runner versions, solver patch, and raw outcomes for reproduction. Fixed locale and
hash seed reduce drift but do not guarantee determinism across kernels, services,
architectures, floating images, or stochastic model backends.

`tools/dashboard.py` is an optional read-only Gradio run explorer. Its hidden-file
display checks names in the saved snapshot; it is a diagnostic inventory, not a
content-based isolation proof or evidence about image history.
