# Bug: `normalize()` does not lowercase

`widget.core.normalize(text)` is documented to **trim whitespace and lowercase** a label, but it only
trims — the returned value keeps its original case.

## Requirements
- `normalize("  HELLO  ")` must return `"hello"`.
- Existing behavior must not regress: `normalize("  hi  ")` must still return `"hi"`.

## Interface
- Module: `widget.core`
- Function: `normalize(text: str) -> str`
