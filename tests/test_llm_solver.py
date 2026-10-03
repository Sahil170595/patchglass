"""LLM solver unit tests — fully offline via an injected HTTP opener (no real Ollama)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from taskbundle.errors import SolverError
from taskbundle.solvers.base import WorkspaceHandle
from taskbundle.solvers.llm import (
    LLMSolver,
    Opener,
    _apply_edits,
    _build_prompt,
    _parse_edits,
    hf_generate,
    ollama_generate,
    openai_generate,
)


def _fake_opener(response_text: str) -> Opener:
    def opener(url: str, body: bytes, headers: dict[str, str]) -> bytes:
        return json.dumps({"response": response_text}).encode("utf-8")

    return opener


def _fake_openai_opener(content: str) -> Opener:
    def opener(url: str, body: bytes, headers: dict[str, str]) -> bytes:
        assert headers.get("Authorization", "").startswith("Bearer ")  # key must be sent
        return json.dumps({"choices": [{"message": {"content": content}}]}).encode("utf-8")

    return opener


def test_ollama_generate_extracts_response() -> None:
    assert ollama_generate("http://x", "m", "p", opener=_fake_opener("hi there")).text == "hi there"


def test_parse_edits() -> None:
    text = "### FILE: a.py\n<<<<<<< SEARCH\nold\n=======\nnew\n>>>>>>> REPLACE\n"
    edits = _parse_edits(text)
    assert len(edits) == 1
    assert (edits[0].path, edits[0].search, edits[0].replace) == ("a.py", "old", "new")


def test_apply_edits_produces_diff_and_edits_file(tmp_path: Path) -> None:
    (tmp_path / "core.py").write_text("def f():\n    return 1\n", encoding="utf-8")
    edits = _parse_edits("### FILE: core.py\n<<<<<<< SEARCH\n    return 1\n=======\n    return 2\n>>>>>>> REPLACE\n")
    outcome = _apply_edits(tmp_path, edits)
    assert "diff --git a/core.py b/core.py" in outcome.patch
    assert "-    return 1" in outcome.patch and "+    return 2" in outcome.patch
    assert outcome.matched == 1 and outcome.applied == 1
    assert (tmp_path / "core.py").read_text(encoding="utf-8") == "def f():\n    return 2\n"


def test_apply_edits_skips_hallucinated_search(tmp_path: Path) -> None:
    (tmp_path / "core.py").write_text("x = 1\n", encoding="utf-8")
    edits = _parse_edits("### FILE: core.py\n<<<<<<< SEARCH\nNOPE\n=======\ny\n>>>>>>> REPLACE\n")
    outcome = _apply_edits(tmp_path, edits)
    assert outcome.patch == "" and outcome.matched == 0  # no corruption when SEARCH doesn't match


def test_classify_patch_taxonomy(tmp_path: Path) -> None:
    from taskbundle.solvers.base import PatchStatus
    from taskbundle.solvers.llm import classify_patch

    (tmp_path / "core.py").write_text("a = 1\n", encoding="utf-8")
    # no edit blocks at all
    assert classify_patch(0, _apply_edits(tmp_path, [])) == PatchStatus.NO_BLOCKS
    # Blocks parsed but SEARCH never matched the available source.
    nomatch = _parse_edits("### FILE: core.py\n<<<<<<< SEARCH\nGHOST\n=======\nz\n>>>>>>> REPLACE\n")
    assert classify_patch(len(nomatch), _apply_edits(tmp_path, nomatch)) == PatchStatus.NO_MATCH
    # matched and changed -> applied
    applied = _parse_edits("### FILE: core.py\n<<<<<<< SEARCH\na = 1\n=======\na = 2\n>>>>>>> REPLACE\n")
    assert classify_patch(len(applied), _apply_edits(tmp_path, applied)) == PatchStatus.APPLIED


def test_ollama_captures_thinking_field_into_reasoning() -> None:
    def opener(url: str, body: bytes, headers: dict[str, str]) -> bytes:
        return json.dumps(
            {"response": "42", "thinking": "Let me add 17 and 25 step by step.", "eval_count": 9}
        ).encode()

    gen = ollama_generate("http://x", "qwen3:14b", "17+25?", opener=opener)
    assert gen.text == "42"
    assert "step by step" in gen.reasoning  # CoT captured from the separate Ollama field


def test_thinking_model_reasoning_lands_in_raw_response_not_parsed_edits(tmp_path: Path) -> None:
    (tmp_path / "core.py").write_text("v = 1\n", encoding="utf-8")
    # the reasoning even DRAFTS an edit block — it must NOT be parsed/applied; only the answer is.
    thinking = (
        "I could write:\n### FILE: core.py\n<<<<<<< SEARCH\nv = 1\n=======\nv = 9\n>>>>>>> REPLACE\nbut actually:"
    )
    answer = "### FILE: core.py\n<<<<<<< SEARCH\nv = 1\n=======\nv = 2\n>>>>>>> REPLACE\n"

    def opener(url: str, body: bytes, headers: dict[str, str]) -> bytes:
        return json.dumps({"response": answer, "thinking": thinking, "eval_count": 50}).encode()

    result = LLMSolver("ollama", "qwen3:14b", opener=opener).solve(WorkspaceHandle(root=tmp_path), "bump v")
    assert result.meta["edits"] == 1 and result.meta["applied"] == 1  # only the answer's edit applied
    assert (tmp_path / "core.py").read_text(encoding="utf-8") == "v = 2\n"  # not v = 9 from the draft
    assert result.raw_response is not None and "<think>" in result.raw_response  # reasoning preserved for audit
    assert result.meta["reasoning_chars"] == len(thinking)


def test_solver_records_raw_response_and_context_and_usage(tmp_path: Path) -> None:
    (tmp_path / "core.py").write_text("v = 1\n", encoding="utf-8")
    response = "<think>change it</think>\n### FILE: core.py\n<<<<<<< SEARCH\nv = 1\n=======\nv = 2\n>>>>>>> REPLACE\n"

    def opener(url: str, body: bytes, headers: dict[str, str]) -> bytes:
        return json.dumps({"response": response, "prompt_eval_count": 50, "eval_count": 30}).encode("utf-8")

    solver = LLMSolver("ollama", "qwen3:14b", opener=opener)
    result = solver.solve(WorkspaceHandle(root=tmp_path), "bump v")
    assert result.raw_response == response  # the full output (reasoning + edits) is preserved
    assert "core.py" in result.context_files  # exactly what the model was shown
    assert result.meta["prompt_tokens"] == 50 and result.meta["completion_tokens"] == 30


def test_solver_retries_on_no_match_then_applies(tmp_path: Path) -> None:
    # Stage 2: a non-applying patch (NO_MATCH) is re-prompted; the next attempt applies.
    (tmp_path / "core.py").write_text("v = 1\n", encoding="utf-8")
    responses = [
        "### FILE: core.py\n<<<<<<< SEARCH\nGHOST\n=======\nz\n>>>>>>> REPLACE\n",  # no match -> retry
        "### FILE: core.py\n<<<<<<< SEARCH\nv = 1\n=======\nv = 2\n>>>>>>> REPLACE\n",  # applies
    ]
    seen: list[str] = []

    def opener(url: str, body: bytes, headers: dict[str, str]) -> bytes:
        seen.append(json.loads(body)["prompt"])
        return json.dumps({"response": responses[min(len(seen) - 1, len(responses) - 1)], "eval_count": 5}).encode()

    result = LLMSolver("ollama", "m", max_attempts=3, opener=opener).solve(WorkspaceHandle(root=tmp_path), "bump v")
    assert len(seen) == 2  # retried once, then succeeded — did not exhaust all 3 attempts
    assert "did not match" in seen[1]  # the 2nd prompt carries the corrective feedback
    assert result.meta["patch_status"] == "applied" and result.meta["attempts"] == 2
    assert result.meta["attempt_statuses"] == "no_match,applied"
    assert (tmp_path / "core.py").read_text(encoding="utf-8") == "v = 2\n"


def test_solver_default_single_attempt_no_retry(tmp_path: Path) -> None:
    (tmp_path / "core.py").write_text("v = 1\n", encoding="utf-8")
    calls = {"n": 0}

    def opener(url: str, body: bytes, headers: dict[str, str]) -> bytes:
        calls["n"] += 1
        return json.dumps({"response": "no edits here"}).encode()  # NO_BLOCKS

    result = LLMSolver("ollama", "m", opener=opener).solve(WorkspaceHandle(root=tmp_path), "x")
    assert calls["n"] == 1 and result.meta["patch_status"] == "no_blocks" and result.meta["attempts"] == 1


def test_solver_records_patch_status_in_meta(tmp_path: Path) -> None:
    (tmp_path / "widget").mkdir()
    (tmp_path / "widget" / "core.py").write_text("def n(t):\n    return t.strip()\n", encoding="utf-8")
    response = (
        "### FILE: widget/core.py\n<<<<<<< SEARCH\n    return t.strip()\n"
        "=======\n    return t.strip().lower()\n>>>>>>> REPLACE\n"
    )
    solver = LLMSolver("ollama", "m", opener=_fake_opener(response))
    result = solver.solve(WorkspaceHandle(root=tmp_path), "lowercase it")
    assert result.meta["patch_status"] == "applied"
    assert result.meta["matched"] == 1 and result.meta["applied"] == 1


def test_solver_end_to_end_offline(tmp_path: Path) -> None:
    (tmp_path / "widget").mkdir()
    (tmp_path / "widget" / "core.py").write_text("def normalize(text):\n    return text.strip()\n", encoding="utf-8")
    response = (
        "### FILE: widget/core.py\n<<<<<<< SEARCH\n    return text.strip()\n"
        "=======\n    return text.strip().lower()\n>>>>>>> REPLACE\n"
    )
    solver = LLMSolver("ollama", "fake-model", opener=_fake_opener(response))
    result = solver.solve(WorkspaceHandle(root=tmp_path), "make normalize lowercase its input")
    assert "return text.strip().lower()" in result.patch
    assert result.meta["edits"] == 1


def test_build_prompt_includes_description_and_files() -> None:
    prompt = _build_prompt("DESC-TOKEN", {"a.py": "code-token"})
    assert "DESC-TOKEN" in prompt and "code-token" in prompt and "SEARCH" in prompt


def test_openai_generate_extracts_content() -> None:
    assert openai_generate("gpt-4o", "p", api_key="sk-test", opener=_fake_openai_opener("hi there")).text == "hi there"


def test_openai_generate_captures_reasoning_token_usage() -> None:
    def opener(url: str, body: bytes, headers: dict[str, str]) -> bytes:
        return json.dumps(
            {
                "choices": [{"message": {"content": "OK"}}],
                "usage": {
                    "prompt_tokens": 1200,
                    "completion_tokens": 800,
                    "completion_tokens_details": {"reasoning_tokens": 600},
                },
            }
        ).encode("utf-8")

    gen = openai_generate("gpt-5.5", "p", api_key="sk-test", reasoning_effort="high", opener=opener)
    assert gen.usage["prompt_tokens"] == 1200
    assert gen.usage["completion_tokens"] == 800
    assert gen.usage["reasoning_tokens"] == 600


def test_openai_generate_requires_key() -> None:
    with pytest.raises(SolverError, match="API key"):
        openai_generate("gpt-4o", "p", api_key=None)


def test_hf_generate_hits_router_and_extracts_content() -> None:
    captured: dict[str, str] = {}

    def opener(url: str, body: bytes, headers: dict[str, str]) -> bytes:
        captured["url"] = url
        assert headers.get("Authorization", "").startswith("Bearer ")
        return json.dumps({"choices": [{"message": {"content": "edit-block"}}]}).encode("utf-8")

    out = hf_generate(
        "deepseek-ai/DeepSeek-R1", "p", api_key="hf_test", base_url="https://router.huggingface.co/v1", opener=opener
    )
    assert out.text == "edit-block"
    assert captured["url"] == "https://router.huggingface.co/v1/chat/completions"


def test_hf_generate_requires_key() -> None:
    with pytest.raises(SolverError, match="API key"):
        hf_generate("deepseek-ai/DeepSeek-R1", "p", api_key=None, base_url="https://router.huggingface.co/v1")


def _capturing_openai_opener(sink: dict[str, object]) -> Opener:
    def opener(url: str, body: bytes, headers: dict[str, str]) -> bytes:
        sink["body"] = json.loads(body.decode("utf-8"))
        return json.dumps({"choices": [{"message": {"content": "OK"}}]}).encode("utf-8")

    return opener


def test_reasoning_effort_is_sent_and_temperature_omitted() -> None:
    """gpt-5.5-style: reasoning_effort goes in the body and temperature is omitted (API rejects temp!=1)."""
    sink: dict[str, object] = {}
    openai_generate("gpt-5.5", "p", api_key="sk-test", reasoning_effort="xhigh", opener=_capturing_openai_opener(sink))
    body = sink["body"]
    assert isinstance(body, dict)
    assert body["reasoning_effort"] == "xhigh"
    assert "temperature" not in body  # reasoning models reject temperature != 1


def test_no_reasoning_effort_keeps_temperature_zero() -> None:
    sink: dict[str, object] = {}
    openai_generate("gpt-4o", "p", api_key="sk-test", opener=_capturing_openai_opener(sink))
    body = sink["body"]
    assert isinstance(body, dict)
    assert body["temperature"] == 0
    assert "reasoning_effort" not in body


def test_reasoning_model_omits_temperature_even_without_effort_flag() -> None:
    # Regression: plain gpt-5.5 (no --reasoning-effort) must NOT send temperature=0 (the API rejects it).
    sink: dict[str, object] = {}
    openai_generate("gpt-5.5", "p", api_key="sk-test", opener=_capturing_openai_opener(sink))
    body = sink["body"]
    assert isinstance(body, dict)
    assert "temperature" not in body  # omitted because gpt-5.5 is a reasoning model
    assert "reasoning_effort" not in body  # none requested -> model uses its default effort


def test_solver_forwards_reasoning_effort_and_records_it_in_meta(tmp_path: Path) -> None:
    (tmp_path / "core.py").write_text("x = 1\n", encoding="utf-8")
    sink: dict[str, object] = {}

    class _Stub:
        openai_api_key = type("S", (), {"get_secret_value": lambda self: "sk-test"})()
        hf_api_key = None
        hf_base_url = "https://router.huggingface.co/v1"
        ollama_host = "http://x"

    solver = LLMSolver(
        "openai",
        "gpt-5.5",
        reasoning_effort="xhigh",
        settings=_Stub(),  # type: ignore[arg-type]
        opener=_capturing_openai_opener(sink),
    )
    result = solver.solve(WorkspaceHandle(root=tmp_path), "noop")
    assert result.meta["reasoning_effort"] == "xhigh"
    body = sink["body"]
    assert isinstance(body, dict)
    assert body["reasoning_effort"] == "xhigh"


def test_default_opener_surfaces_http_error_body(monkeypatch: pytest.MonkeyPatch) -> None:
    """A 400 from the API (bad model / invalid effort) surfaces the body, not a generic 'could not reach'."""
    import io
    import urllib.error
    import urllib.request
    from email.message import Message

    from taskbundle.solvers.llm import _default_opener

    def _raise(*_a: object, **_k: object) -> object:
        raise urllib.error.HTTPError(
            "https://api.openai.com/v1/chat/completions",
            400,
            "Bad Request",
            Message(),
            io.BytesIO(b'{"error":{"message":"Unsupported value: reasoning_effort"}}'),
        )

    monkeypatch.setattr(urllib.request, "urlopen", _raise)
    with pytest.raises(SolverError, match="HTTP 400.*Unsupported value"):
        _default_opener("https://api.openai.com/v1/chat/completions", b"{}", {})


def test_llm_solver_routes_reasoning_thinking_blocks_are_ignored(tmp_path: Path) -> None:
    """A reasoning model's <think> preamble must not break SEARCH/REPLACE parsing."""
    (tmp_path / "core.py").write_text("def f():\n    return 1\n", encoding="utf-8")
    thinking_response = (
        "<think>\nThe bug is the return value; I should change 1 to 2.\n</think>\n"
        "### FILE: core.py\n<<<<<<< SEARCH\n    return 1\n=======\n    return 2\n>>>>>>> REPLACE\n"
    )
    solver = LLMSolver("ollama", "qwen3:14b", opener=_fake_opener(thinking_response))
    result = solver.solve(WorkspaceHandle(root=tmp_path), "return 2 instead of 1")
    assert result.meta["edits"] == 1
    assert "+    return 2" in result.patch
