"""LLM test-writer solver (`llm-synth:ollama/<model>`) for test synthesis.

Offline units cover the extraction + new-file-patch helpers. The e2e test mocks the model call (so it is
deterministic and does not depend on a local model's competence) and grades the produced test on real
Docker. This test is not evidence of a live model's performance.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from taskbundle.containers.client import DockerRuntime
from taskbundle.errors import TaskBundleError
from taskbundle.harness.run import run_task
from taskbundle.solvers import llm_synth
from taskbundle.solvers.llm import Generation
from taskbundle.solvers.llm_synth import _extract_code, _new_file_patch

SRC = Path(__file__).resolve().parent.parent / "examples" / "hello-bug-synthesis"

_GOOD_TEST = (
    "```python\nfrom widget.core import normalize\n\n\ndef test_repro():\n    assert normalize('AB') == 'ab'\n```"
)


# --------------------------------------------------------------------------- helpers (offline)
def test_extract_code_strips_think_and_fences() -> None:
    raw = "<think>reason</think>\nHere it is:\n```python\ndef test_x():\n    assert True\n```\n"
    assert _extract_code(raw) == "def test_x():\n    assert True"


def test_extract_code_unfenced_passthrough() -> None:
    assert _extract_code("def test_y():\n    assert 1\n") == "def test_y():\n    assert 1"


def test_new_file_patch_shape() -> None:
    patch = _new_file_patch("tests/t.py", "a\nb\n")
    assert "new file mode 100644" in patch
    assert "+++ b/tests/t.py" in patch
    assert "@@ -0,0 +1,2 @@" in patch
    assert "+a\n+b" in patch


# --------------------------------------------------------------------------- e2e (real docker, mocked model)
@pytest.fixture(scope="module")
def _docker() -> None:
    if not (SRC / "task.json").exists() or not (SRC.parent / "hello-bug" / "task.json").exists():
        pytest.skip("run examples/hello-bug/build.py first (the synthesis bundle reuses its image)")
    try:
        DockerRuntime()
    except TaskBundleError as exc:
        pytest.skip(f"docker not available: {exc}")


def test_llm_synth_pipeline_resolves(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, _docker: None) -> None:
    # Mock ONLY the model call; the extraction -> new-file patch -> discover -> grade -> dC path is real.
    monkeypatch.setattr(
        llm_synth,
        "ollama_generate",
        lambda host, model, prompt: Generation(
            text=_GOOD_TEST, usage={"prompt_tokens": 1, "completion_tokens": 1, "reasoning_tokens": 0}
        ),
    )
    bundle = tmp_path / "syn"
    shutil.copytree(SRC, bundle)
    outcome = run_task(bundle, "llm-synth:ollama/gemma2:2b")
    assert outcome.report.resolved is True  # the model's reproducing test is fail->pass vs the golden patch
    assert any("dC=1.00" in w for w in outcome.report.warnings)  # and it executes the changed line
