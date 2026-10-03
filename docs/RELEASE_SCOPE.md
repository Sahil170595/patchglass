# Public release scope

This repository starts with one clean root commit and no inherited history.
It preserves the full first-party implementation rather than substituting a browser
adaptation. Public documentation is written for this release.

Included source surfaces:

- `taskbundle/`: schema/loader/scaffolding, Docker image/runtime/tar transport,
  repair and synthesis grading, validation, solvers, eight parser formats,
  public benchmark import adapter, SQLite/reporting/suite execution, and CLI.
- `tests/`: original unit and Docker integration coverage, authored protocol
  fixtures, and release-specific host-write confinement tests.
- `examples/`: only `hello-bug`, `hello-bug-synthesis`, `finance-interest`,
  `security-pathcheck`, and `go-demo-pow`, including build scripts, baseline source,
  test buckets, and reference patches.
- `tools/dashboard.py`: optional run explorer; `tools/test_offline.py`: bounded
  release qualification without containers or network.
- `pyproject.toml`, `uv.lock`, Git ignore/line-ending rules, MIT license, unchanged bundled
  seccomp profile, full Apache-2.0 license, and third-party attribution.

Excluded: external benchmark task fixtures, uncertain dataset material, historical
planning/research notes, operational run JSON, provider transcripts, execution logs,
local environment files, credentials, caches, dependencies, and image layers.
Parser compatibility tests use newly authored sanitizer strings rather than captured
benchmark reproduction output. Dependency and benchmark service URLs are public
upstream interfaces, not organization-specific deployment endpoints.

Release-copy changes preserve the harness algorithms: public branding/docs/notices,
removal of stale internal planning references and unsupported comments, synthetic
fixture replacement (including synthetic import rows and relocated-source snippets),
the offline test entry point, and a tested confinement
check for host-side model edit paths. No trained weights or benchmark scores are
introduced. This is an independent open-source project; references to upstream tools
do not imply collaboration, delivery, endorsement, or affiliation.
