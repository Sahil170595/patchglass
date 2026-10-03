# Bug: `safe_join()` allows path traversal

`safeio.paths.safe_join(base_dir, user_path)` must return a path INSIDE `base_dir`, or `None` if the
`user_path` escapes it. It currently returns the joined path even when `user_path` contains `../`
sequences that climb out of `base_dir` (a directory-traversal vulnerability).

## Requirements
- `safe_join("/srv/data", "../../etc/passwd")` must return `None`.
- A legitimate path must still work: `safe_join("/srv/data", "report.csv")` returns `"/srv/data/report.csv"`.

## Interface
- Module: `safeio.paths`
- Function: `safe_join(base_dir: str, user_path: str) -> str | None`
