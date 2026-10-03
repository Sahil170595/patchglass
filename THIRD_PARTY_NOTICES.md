# Third-party notices

## First-party material

Patchglass code, tests, documentation, and the five synthetic example bundles are
licensed under the root MIT license, retaining the original copyright notice.
The example identity `tb@example.com` and `example.com` module names are deliberate
synthetic placeholders, not contact or service credentials.

## Bundled Moby seccomp profile

`seccomp/default.json` is an unchanged policy snapshot from Moby's profiles project.
Its parsed JSON matches the upstream profile at commit
`ea2916d01bce39361d6f9b1aae098fca7f73a6ce` (2023-06-28):

- [Pinned profile](https://github.com/moby/profiles/blob/ea2916d01bce39361d6f9b1aae098fca7f73a6ce/seccomp/default.json)
- [Upstream Apache-2.0 license](https://github.com/moby/profiles/blob/2ceae35d351c156cb5a8efc0fdc4a08cf94569d8/LICENSE)
- Full license copy: [licenses/Apache-2.0.txt](licenses/Apache-2.0.txt)

The historical extracted profile commit predates the license file in that repository;
the license is retained from the upstream profiles repository at the separately pinned
revision above. Attribution belongs to the Moby project contributors. No NOTICE or
file-level copyright statement was present alongside that historical profile.
This is an older snapshot, not the latest syscall policy or a security certification.

## Dependencies and external inputs

Python packages listed in `pyproject.toml`/`uv.lock` are dependencies, not bundled
source. Their licenses remain their own. The example Docker base images, system
packages, and test tools likewise have separate upstream licenses.

The importer references public SWE-bench Pro dataset, image, and runner locations.
It does not redistribute their datasets, upstream task repositories, image layers,
or scripts in this release. Users must check source and fixture licenses and access
terms before importing or redistributing tasks. In particular, importing GPL-covered
repositories does not relicense them as MIT. External benchmark examples are not
included; only the five original synthetic bundles are distributed.

Protocol tests use hand-authored mock responses and synthetic runner strings, not
captured provider traffic or benchmark execution logs. Public upstream identifiers
are retained only where required by the public adapter's interfaces. Importer rows,
relocated-test snippets, and parser reproduction strings are independently authored.
