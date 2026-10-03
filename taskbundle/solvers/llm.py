"""LLM solver — provider-pluggable, called from the HOST so the solver container stays network-isolated.

Ollama (local, free, offline) is the default. Small models botch raw unified diffs, so we ask for
`### FILE:` + SEARCH/REPLACE blocks and compute the diff ourselves. temperature=0 + a fixed seed for
best-effort reproducibility; the produced patch is recorded so the graded verdict is reproducible
from the artifact regardless.
"""

from __future__ import annotations

import difflib
import json
import re
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path, PureWindowsPath
from typing import Final

from taskbundle.errors import SolverError
from taskbundle.settings import Settings, get_settings
from taskbundle.solvers.base import PatchStatus, SolverResult, WorkspaceHandle
from taskbundle.solvers.localize import select_files

Opener = Callable[[str, bytes, dict[str, str]], bytes]

LLM_TIMEOUT_S: Final = 600  # local generation can be slow for a 12B model (reasoning models slower still).
OPENAI_URL: Final = "https://api.openai.com/v1/chat/completions"
_JSON_HEADERS: Final = {"Content-Type": "application/json"}
_PROVIDERS: Final = ("ollama", "openai", "huggingface")
_MAX_FILE_BYTES: Final = 40_000  # cap per-file context in the prompt.
_MAX_TOTAL_BYTES: Final = 200_000  # cap total prompt context (small repos / the synthetic task).
# Localization selects the *right* files, so a much tighter budget concentrates on the fix region
# (less noise -> better edits, fewer tokens) instead of filling 200 KB with the flat slice.
_LOCALIZE_MAX_BYTES: Final = 80_000
_HTTP_ERROR_BODY_CHARS: Final = 500  # how much of an API error body to surface (actionable, not a wall).
# OpenAI reasoning families reject temperature != 1 ("Only the default (1) value is supported"), even with
# no reasoning_effort set — so temperature must be OMITTED for them, not just when an effort flag is passed.
_OPENAI_REASONING_PREFIXES: Final = ("gpt-5", "o1", "o3", "o4")


def _is_reasoning_model(model: str) -> bool:
    return model.lower().startswith(_OPENAI_REASONING_PREFIXES)


_CODE_SUFFIXES: Final = (".py", ".js", ".ts", ".go", ".java", ".rb", ".rs", ".c", ".cpp", ".h")
_SKIP_DIRS: Final = {".git", "node_modules", "__pycache__", ".venv", "dist", "build"}

# "### FILE: path" then an Aider-style SEARCH/REPLACE block.
_BLOCK_RE = re.compile(
    r"###\s*FILE:\s*(?P<path>[^\n]+)\n<{5,9}\s*SEARCH\n(?P<search>.*?)\n={5,9}\n(?P<replace>.*?)\n>{5,9}\s*REPLACE",
    re.DOTALL,
)


@dataclass
class _Edit:
    path: str
    search: str
    replace: str


@dataclass
class Generation:
    """A model response + normalized token usage (for observability / cost telemetry)."""

    text: str
    usage: dict[str, int]  # prompt_tokens, completion_tokens, reasoning_tokens (0 when the API omits them).
    reasoning: str = ""  # chain-of-thought text when the provider exposes it (Ollama thinking models).


def _empty_usage() -> dict[str, int]:
    return {"prompt_tokens": 0, "completion_tokens": 0, "reasoning_tokens": 0}


def _format_raw(generation: Generation) -> str:
    """The observability artifact: the model's reasoning (when exposed) followed by its answer."""
    if generation.reasoning:
        return f"<think>\n{generation.reasoning}\n</think>\n\n{generation.text}"
    return generation.text


def _default_opener(url: str, body: bytes, headers: dict[str, str]) -> bytes:
    request = urllib.request.Request(url, data=body, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=LLM_TIMEOUT_S) as response:  # noqa: S310
            return bytes(response.read())
    except urllib.error.HTTPError as exc:
        # Surface the API's actual error body (bad model id / invalid reasoning_effort / temperature
        # rejected) instead of letting it collapse into a generic "could not reach" — no silent failure.
        detail = exc.read().decode("utf-8", "replace")[:_HTTP_ERROR_BODY_CHARS]
        raise SolverError(f"HTTP {exc.code} from {url}: {detail}") from exc


def ollama_generate(host: str, model: str, prompt: str, *, opener: Opener = _default_opener) -> Generation:
    """Call Ollama's /api/generate (non-streaming) and return the model's text + token usage."""
    url = f"{host.rstrip('/')}/api/generate"
    body = json.dumps(
        {"model": model, "prompt": prompt, "stream": False, "options": {"temperature": 0, "seed": 0}}
    ).encode("utf-8")
    try:
        raw = opener(url, body, _JSON_HEADERS)
    except OSError as exc:
        raise SolverError(f"could not reach Ollama at {host} (is it running? `ollama serve`): {exc}") from exc
    try:
        data = json.loads(raw)
        text = str(data["response"])
    except (json.JSONDecodeError, KeyError, TypeError) as exc:
        raise SolverError(f"unexpected Ollama response: {exc}") from exc
    usage = _empty_usage()
    usage["prompt_tokens"] = int(data.get("prompt_eval_count", 0))
    usage["completion_tokens"] = int(data.get("eval_count", 0))  # includes thinking tokens (Ollama does not split).
    return Generation(text=text, usage=usage, reasoning=str(data.get("thinking", "") or ""))


def _chat_completion(
    url: str,
    model: str,
    prompt: str,
    *,
    api_key: str | None,
    label: str,
    opener: Opener,
    reasoning_effort: str | None = None,
    temperature: float | None = 0.0,
) -> Generation:
    """Call an OpenAI-compatible /chat/completions endpoint and return the text + token usage.

    Shared by OpenAI and HuggingFace's Inference-Providers router (same wire format); a reasoning
    model's <think> preamble rides along in `content` and is ignored by the SEARCH/REPLACE parser.
    `temperature=None` omits the field entirely — reasoning models reject temperature != 1.
    """
    if not api_key:
        raise SolverError(f"{label} solver needs an API key — set TASKBUNDLE_{label.upper()}_API_KEY")
    payload: dict[str, object] = {"model": model, "messages": [{"role": "user", "content": prompt}]}
    if reasoning_effort:
        payload["reasoning_effort"] = reasoning_effort
    if temperature is not None:
        payload["temperature"] = temperature
    body = json.dumps(payload).encode("utf-8")
    headers = {**_JSON_HEADERS, "Authorization": f"Bearer {api_key}"}
    try:
        raw = opener(url, body, headers)
    except OSError as exc:
        raise SolverError(f"could not reach {label}: {exc}") from exc
    try:
        data = json.loads(raw)
        text = str(data["choices"][0]["message"]["content"])
    except (json.JSONDecodeError, KeyError, IndexError, TypeError) as exc:
        raise SolverError(f"unexpected {label} response: {exc}") from exc
    return Generation(text=text, usage=_usage_from_openai(data.get("usage")))


def _usage_from_openai(usage: object) -> dict[str, int]:
    """Normalize an OpenAI/HF `usage` block into our token-usage dict (reasoning models report extra)."""
    out = _empty_usage()
    if isinstance(usage, dict):
        out["prompt_tokens"] = int(usage.get("prompt_tokens", 0) or 0)
        out["completion_tokens"] = int(usage.get("completion_tokens", 0) or 0)
        details = usage.get("completion_tokens_details")
        if isinstance(details, dict):
            out["reasoning_tokens"] = int(details.get("reasoning_tokens", 0) or 0)
    return out


def openai_generate(
    model: str,
    prompt: str,
    *,
    api_key: str | None,
    reasoning_effort: str | None = None,
    opener: Opener = _default_opener,
) -> Generation:
    """Call OpenAI's chat completions and return text + usage (reasoning_effort for gpt-5-series)."""
    # Omit temperature for reasoning models (they reject != 1) or when an effort is set; else temperature=0.
    temperature = None if (reasoning_effort or _is_reasoning_model(model)) else 0.0
    return _chat_completion(
        OPENAI_URL,
        model,
        prompt,
        api_key=api_key,
        label="OPENAI",
        opener=opener,
        reasoning_effort=reasoning_effort,
        temperature=temperature,
    )


def hf_generate(
    model: str,
    prompt: str,
    *,
    api_key: str | None,
    base_url: str,
    reasoning_effort: str | None = None,
    opener: Opener = _default_opener,
) -> Generation:
    """Call HF Inference Providers' OpenAI-compatible router and return text + usage."""
    url = f"{base_url.rstrip('/')}/chat/completions"
    temperature = None if reasoning_effort else 0.0  # HF reasoning models that need it set their own effort.
    return _chat_completion(
        url,
        model,
        prompt,
        api_key=api_key,
        label="HF",
        opener=opener,
        reasoning_effort=reasoning_effort,
        temperature=temperature,
    )


def _gather_files(root: Path) -> dict[str, str]:
    """Collect code files (relative path -> content) under the size caps for the prompt."""
    files: dict[str, str] = {}
    total = 0
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.suffix not in _CODE_SUFFIXES:
            continue
        if any(part in _SKIP_DIRS for part in path.relative_to(root).parts):
            continue
        try:
            data = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        if len(data) > _MAX_FILE_BYTES or total + len(data) > _MAX_TOTAL_BYTES:
            continue
        files[path.relative_to(root).as_posix()] = data
        total += len(data)
    return files


def _build_prompt(description: str, files: dict[str, str]) -> str:
    listing = "\n\n".join(f"### FILE: {path}\n```\n{content}\n```" for path, content in files.items())
    return (
        "You are a senior engineer. Fix the issue below by editing the repository files.\n\n"
        "Respond ONLY with one or more edit blocks in EXACTLY this format (no prose):\n"
        "### FILE: <relative/path>\n<<<<<<< SEARCH\n<exact lines to replace>\n=======\n"
        "<replacement lines>\n>>>>>>> REPLACE\n\n"
        "The SEARCH text must match the file's current content EXACTLY.\n\n"
        f"# Issue\n{description}\n\n# Repository files\n{listing}\n"
    )


def _parse_edits(text: str) -> list[_Edit]:
    return [
        _Edit(path=m.group("path").strip(), search=m.group("search"), replace=m.group("replace"))
        for m in _BLOCK_RE.finditer(text)
    ]


@dataclass
class _ApplyOutcome:
    """Result of applying edits: the diff + how many edits matched file content vs. actually changed it."""

    patch: str
    matched: int  # edits whose SEARCH text was found in a real file.
    applied: int  # of those, how many produced an actual change (written to disk).


def _apply_edits(root: Path, edits: list[_Edit]) -> _ApplyOutcome:
    """Apply SEARCH/REPLACE edits to the working tree; return the diff + match/apply counts."""
    chunks: list[str] = []
    matched = applied = 0
    root = root.resolve()
    for edit in edits:
        relative = Path(edit.path)
        windows = PureWindowsPath(edit.path)
        if relative.is_absolute() or windows.drive or windows.root or ".." in relative.parts:
            continue
        target = (root / relative).resolve()
        if not target.is_relative_to(root) or ".git" in target.relative_to(root).parts:
            continue
        if not target.is_file():
            continue
        before = target.read_text(encoding="utf-8")
        if edit.search not in before:
            continue  # model hallucinated context; skip rather than corrupt the file.
        matched += 1
        after = before.replace(edit.search, edit.replace, 1)
        if after == before:
            continue  # matched but a no-op (search == replace).
        target.write_text(after, encoding="utf-8")
        applied += 1
        diff = difflib.unified_diff(
            before.splitlines(keepends=True),
            after.splitlines(keepends=True),
            fromfile=f"a/{edit.path}",
            tofile=f"b/{edit.path}",
        )
        chunks.append(f"diff --git a/{edit.path} b/{edit.path}\n" + "".join(diff))
    return _ApplyOutcome(patch="".join(chunks), matched=matched, applied=applied)


def classify_patch(num_edits: int, outcome: _ApplyOutcome) -> PatchStatus:
    """Map (edits parsed, apply outcome) onto the diagnostic taxonomy."""
    if num_edits == 0:
        return PatchStatus.NO_BLOCKS
    if outcome.matched == 0:
        return PatchStatus.NO_MATCH
    if outcome.applied == 0:
        return PatchStatus.EMPTY_DIFF
    return PatchStatus.APPLIED


# Applicability feedback re-prompted on a non-applying attempt (Stage 2). Host-side only — no test
# execution; applicability feedback uses the extracted workspace, not grading output.
_RETRY_FEEDBACK: Final[dict[PatchStatus, str]] = {
    PatchStatus.NO_BLOCKS: "\n\n# Retry\nYour previous reply contained no valid edit block. Reply with ONLY one or "
    "more `### FILE: <path>` + `<<<<<<< SEARCH` / `=======` / `>>>>>>> REPLACE` blocks — no prose.",
    PatchStatus.NO_MATCH: "\n\n# Retry\nYour previous SEARCH text did not match any file exactly, so nothing was "
    "applied. Copy the EXACT current lines from the files shown above into the SEARCH block.",
    PatchStatus.EMPTY_DIFF: "\n\n# Retry\nYour edit was a no-op (SEARCH equalled REPLACE); make a real change.",
}


def retry_feedback(status: PatchStatus) -> str:
    """The corrective hint appended to the prompt when an attempt did not apply (empty when none applies)."""
    return _RETRY_FEEDBACK.get(status, "")


class LLMSolver:
    """Generate a patch via an LLM. provider='ollama' (local, default) | 'openai' (needs a key)."""

    name = "llm"

    def __init__(
        self,
        provider: str,
        model: str,
        *,
        reasoning_effort: str | None = None,
        localize: bool = False,
        localize_budget: int | None = None,
        max_attempts: int = 1,
        visible_tests: list[str] | None = None,
        settings: Settings | None = None,
        opener: Opener = _default_opener,
    ) -> None:
        if provider not in _PROVIDERS:
            raise SolverError(f"llm provider {provider!r} not supported; use one of {_PROVIDERS}")
        self._provider = provider
        self._model = model
        self._reasoning_effort = reasoning_effort  # e.g. 'xhigh' for gpt-5.5; ignored by ollama.
        self._localize = localize  # rank+select the right files instead of a flat truncated dump.
        self._localize_budget = localize_budget or _LOCALIZE_MAX_BYTES  # tighten to fit a model's context/TPM.
        self._max_attempts = max(1, max_attempts)  # Stage 2: re-prompt on a non-applying patch.
        self._visible_tests = visible_tests or []
        self._settings = settings or get_settings()
        self._opener = opener

    def solve(self, workspace: WorkspaceHandle, description: str) -> SolverResult:
        if self._localize:
            files = select_files(
                workspace.root, description, visible_tests=self._visible_tests, max_total_bytes=self._localize_budget
            )
        else:
            files = _gather_files(workspace.root)
        base_prompt = _build_prompt(description, files)
        total_usage = _empty_usage()
        feedback = ""
        attempt_statuses: list[str] = []
        generation = Generation(text="", usage=_empty_usage())
        outcome = _ApplyOutcome(patch="", matched=0, applied=0)
        status = PatchStatus.NO_BLOCKS
        for _attempt in range(self._max_attempts):
            generation = self._generate(base_prompt + feedback)
            for key in total_usage:
                total_usage[key] += generation.usage[key]
            edits = _parse_edits(generation.text)  # parse the ANSWER only; reasoning is kept separate.
            outcome = _apply_edits(workspace.root, edits)  # only applies on APPLIED; non-apply leaves tree untouched.
            status = classify_patch(len(edits), outcome)
            attempt_statuses.append(status.value)
            if status == PatchStatus.APPLIED:
                break
            feedback = retry_feedback(status)
        meta: dict[str, str | int | float] = {
            "solver": f"llm:{self._provider}/{self._model}",
            "edits": len(_parse_edits(generation.text)),
            "matched": outcome.matched,
            "applied": outcome.applied,
            "patch_status": status.value,
            "context_files": len(files),
            "localize": int(self._localize),
            "attempts": len(attempt_statuses),
            "prompt_tokens": total_usage["prompt_tokens"],
            "completion_tokens": total_usage["completion_tokens"],
            "reasoning_tokens": total_usage["reasoning_tokens"],
        }
        if len(attempt_statuses) > 1:
            meta["attempt_statuses"] = ",".join(attempt_statuses)
        if self._reasoning_effort:
            meta["reasoning_effort"] = self._reasoning_effort
        if generation.reasoning:
            meta["reasoning_chars"] = len(generation.reasoning)
        return SolverResult(
            patch=outcome.patch, meta=meta, raw_response=_format_raw(generation), context_files=list(files.keys())
        )

    def _generate(self, prompt: str) -> Generation:
        if self._provider == "openai":
            key = self._settings.openai_api_key.get_secret_value() if self._settings.openai_api_key else None
            return openai_generate(
                self._model, prompt, api_key=key, reasoning_effort=self._reasoning_effort, opener=self._opener
            )
        if self._provider == "huggingface":
            key = self._settings.hf_api_key.get_secret_value() if self._settings.hf_api_key else None
            return hf_generate(
                self._model,
                prompt,
                api_key=key,
                base_url=self._settings.hf_base_url,
                reasoning_effort=self._reasoning_effort,
                opener=self._opener,
            )
        return ollama_generate(self._settings.ollama_host, self._model, prompt, opener=self._opener)
