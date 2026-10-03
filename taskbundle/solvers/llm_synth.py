"""LLM test-writer solver for `kind: test-synthesis`.

Prompts a model to write a *reproducing test* from the issue (not a code fix) and emits it as a new test
file. Ollama is the shipped offline path (`llm-synth:ollama/<model>`); the same shape extends to the API
providers behind `get_settings()`. Deterministic where the backend allows (temperature 0 + fixed seed).
"""

from __future__ import annotations

import re
from typing import Final

from taskbundle.errors import SolverError
from taskbundle.settings import Settings, get_settings
from taskbundle.solvers.base import SolverResult, WorkspaceHandle
from taskbundle.solvers.llm import _gather_files, ollama_generate

_DEFAULT_TEST_PATH: Final = "tests/test_llm_repro.py"
_MAX_CONTEXT_FILES: Final = 6  # keep the prompt small for tiny local models.
_MAX_CONTEXT_BYTES: Final = 12_000

_PROMPT: Final = """You are writing a single pytest test that REPRODUCES a bug.

Issue:
{description}

Relevant source files (import from these real modules):
{files}

Write exactly ONE pytest test function that FAILS on the current (buggy) code and PASSES once the bug is
fixed. Include ALL necessary imports (e.g. `from <module> import <name>` for every symbol you call). Do
NOT modify any source file. Output ONLY the Python test file content — no prose, no markdown fences, no
explanation."""

_FENCE_RE: Final = re.compile(r"```(?:python)?\s*\n(.*?)```", re.DOTALL)
_THINK_RE: Final = re.compile(r"<think>.*?</think>", re.DOTALL)
_TEST_NAME_HINT: Final = ("test", "tests", "conftest")


class SynthLLMSolver:
    """Emit a reproducing TEST patch written by an LLM (the test-synthesis analogue of the LLM fix solver)."""

    def __init__(
        self,
        provider: str,
        model: str,
        *,
        test_path: str = _DEFAULT_TEST_PATH,
        settings: Settings | None = None,
    ) -> None:
        if provider != "ollama":
            raise SolverError(f"llm-synth currently supports provider 'ollama', got {provider!r}")
        self._model = model
        self._test_path = test_path
        self._settings = settings or get_settings()
        self.name = f"llm-synth:{provider}/{model}"

    def solve(self, workspace: WorkspaceHandle, description: str) -> SolverResult:
        prompt = _PROMPT.format(description=description.strip(), files=_context(_gather_files(workspace.root)))
        gen = ollama_generate(self._settings.ollama_host, self._model, prompt)
        code = _extract_code(gen.text)
        patch = _new_file_patch(self._test_path, code) if code.strip() else ""
        return SolverResult(patch=patch, meta={"solver": self.name, **gen.usage}, raw_response=gen.text)


def _context(files: dict[str, str]) -> str:
    """A small, test-free slice of the repo for the prompt (tiny models need a short prompt)."""
    picked: list[str] = []
    used = 0
    for path, body in files.items():
        if any(part in _TEST_NAME_HINT for part in path.split("/")):
            continue
        chunk = f"# {path}\n{body}"
        if used + len(chunk) > _MAX_CONTEXT_BYTES or len(picked) >= _MAX_CONTEXT_FILES:
            break
        picked.append(chunk)
        used += len(chunk)
    return "\n\n".join(picked) or "(no source files available)"


def _extract_code(text: str) -> str:
    """Pull the test code out of the model's output: strip <think>, prefer a fenced block, else the text."""
    text = _THINK_RE.sub("", text).strip()
    fence = _FENCE_RE.search(text)
    return (fence.group(1) if fence else text).strip()


def _new_file_patch(path: str, body: str) -> str:
    """Wrap `body` as a unified diff that ADDS a new file at `path`."""
    lines = body.rstrip("\n").split("\n")
    hunk = "\n".join("+" + ln for ln in lines)
    return (
        f"diff --git a/{path} b/{path}\n"
        f"new file mode 100644\n--- /dev/null\n+++ b/{path}\n"
        f"@@ -0,0 +1,{len(lines)} @@\n{hunk}\n"
    )
