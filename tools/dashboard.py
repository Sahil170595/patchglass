"""Read-only Gradio dashboard over the Task Bundle CLI run history.

A VISUALIZATION EXTRA — not part of the graded core. It opens each bundle's
`<bundle>/.taskbundle/taskbundle.db` plus the on-disk `runs/<id>/` artifacts and renders them; it never
writes, and never imports the harness internals (so it cannot perturb a verdict). `gradio` is imported
lazily inside `build_ui`, so this module's data layer is usable — and testable — without the extra.

Run:  uv pip install -e ".[viz]"  &&  uv run python tools/dashboard.py
"""

from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
EXAMPLES_DIR = REPO_ROOT / "examples"
DB_GLOB = "*/.taskbundle/taskbundle.db"

# run.json keys we surface; reading the artifact (when present) beats the DB row for the four buckets.
_RUN_COLUMNS = ("run_id", "task_id", "solver", "model", "verdict", "resolved", "created_at", "cost_json")
_RESOLVED_BADGE = {1: "🟢 RESOLVED", 0: "🔴 not-resolved", None: "⚪ —"}

# Bump the default Gradio type scale up — the stock sizes are small for a data-dense dashboard.
_CSS = """
.gradio-container { font-size: 17px; }
.gradio-container .prose * { font-size: 16px !important; line-height: 1.6 !important; }
.gradio-container .prose h1 { font-size: 30px !important; }
.gradio-container .prose h2 { font-size: 23px !important; }
.gradio-container table th,
.gradio-container table td { font-size: 15px !important; padding: 8px 10px !important; }
.gradio-container label,
.gradio-container .label-wrap span { font-size: 16px !important; }
.gradio-container .cm-editor .cm-content,
.gradio-container pre,
.gradio-container code { font-size: 14.5px !important; }
.gradio-container input { font-size: 16px !important; }
"""


# --------------------------------------------------------------------------- data layer (no gradio)
def find_databases(root: Path) -> list[Path]:
    """Every bundle DB under `root` (each bundle owns its own store)."""
    return sorted(root.glob(DB_GLOB))


def bundle_root(db: Path) -> Path:
    """`<bundle>/.taskbundle/taskbundle.db` -> `<bundle>`."""
    return db.parent.parent


def artifacts_dir(db: Path, run_id: str) -> Path:
    return db.parent / "runs" / run_id


def _connect_ro(db: Path) -> sqlite3.Connection:
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    return con


def _cost_summary(cost_json: str | None) -> str:
    if not cost_json:
        return ""
    try:
        cost = json.loads(cost_json)
    except (json.JSONDecodeError, TypeError):
        return ""
    bits = []
    if cost.get("solver"):
        bits.append(str(cost["solver"]))
    for key in ("prompt_tokens", "completion_tokens", "reasoning_tokens"):
        if cost.get(key):
            bits.append(f"{key.split('_')[0]}={int(cost[key])}")
    if cost.get("attempts"):
        bits.append(f"attempts={cost['attempts']}")
    return " · ".join(bits)


def load_runs(db: Path) -> list[dict[str, Any]]:
    """All runs in one bundle DB, newest first. Defensive against older DBs missing a column."""
    name = bundle_root(db).name
    with _connect_ro(db) as con:
        try:
            rows = con.execute("SELECT * FROM runs ORDER BY created_at DESC").fetchall()
        except sqlite3.Error:
            return []
    out: list[dict[str, Any]] = []
    for row in rows:
        keys = row.keys()
        rec = {col: (row[col] if col in keys else None) for col in _RUN_COLUMNS}
        rec["bundle"] = name
        rec["db"] = str(db)
        rec["cost"] = _cost_summary(rec.get("cost_json"))
        out.append(rec)
    return out


def all_runs(root: Path) -> list[dict[str, Any]]:
    return [rec for db in find_databases(root) for rec in load_runs(db)]


def _bundle_buckets(db: Path) -> dict[str, list[str]]:
    task_json = bundle_root(db) / "task.json"
    try:
        spec = json.loads(task_json.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"pass2pass": [], "fail2pass": []}
    buckets = spec.get("buckets", {})
    return {"pass2pass": buckets.get("pass2pass", []), "fail2pass": buckets.get("fail2pass", [])}


def _bundle_domain(db: Path) -> str:
    task_json = bundle_root(db) / "task.json"
    try:
        return str(json.loads(task_json.read_text(encoding="utf-8")).get("domain", "swe"))
    except (OSError, json.JSONDecodeError):
        return "swe"


def load_test_results(db: Path, run_id: str) -> list[tuple[str, str, str]]:
    with _connect_ro(db) as con:
        try:
            rows = con.execute(
                "SELECT bucket, outcome, test_id FROM test_results WHERE run_id = ? ORDER BY bucket, test_id",
                (run_id,),
            ).fetchall()
        except sqlite3.Error:
            return []
    return [(r["bucket"], r["outcome"], r["test_id"]) for r in rows]


def _read_run_json(db: Path, run_id: str) -> dict[str, Any] | None:
    path = artifacts_dir(db, run_id) / "run.json"
    try:
        return dict(json.loads(path.read_text(encoding="utf-8")))
    except (OSError, json.JSONDecodeError):
        return None


def _read_text(path: Path, limit: int = 20000) -> str:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    return text if len(text) <= limit else text[:limit] + "\n… (truncated)"


def _isolation_proof(db: Path, run_id: str) -> str:
    """Display a filename inventory; this does not prove content or image-history isolation."""
    view = artifacts_dir(db, run_id) / "solver_view"
    if not view.is_dir():
        return "_(no `solver_view/` snapshot for this run — solver did not run, or artifacts pruned)_"
    seen = {p.name for p in view.rglob("*") if p.is_file()}
    buckets = _bundle_buckets(db)
    graded = list(buckets["fail2pass"]) + list(buckets["pass2pass"])
    lines = ["**Saved solver view - hidden-file name inventory**", ""]
    files = sorted(p.relative_to(view).as_posix() for p in view.rglob("*") if p.is_file())
    lines.append("Solver-view tree (the frozen snapshot the model received):")
    lines += [f"- `{f}`" for f in files[:40]]
    lines.append("")
    lines.append("Graded (hidden) test ids and whether their file leaked into that tree:")
    for nodeid in graded:
        fname = nodeid.split("::")[0].split("/")[-1]
        leaked = fname in seen
        mark = "🔴 PRESENT (leak!)" if leaked else "🟢 absent (stripped)"
        lines.append(f"- `{nodeid}` → {mark}")
    if not graded:
        lines.append("- _(this bundle declares no graded buckets)_")
    return "\n".join(lines)


def run_detail(db_path: str, run_id: str) -> tuple[str, str, list[list[str]], str, str]:
    """(header_md, buckets_md, per_test_rows, patch_text, isolation_md) for one run."""
    db = Path(db_path)
    report = _read_run_json(db, run_id)
    run_row: dict[str, Any] = {}
    with _connect_ro(db) as con:
        row = con.execute("SELECT * FROM runs WHERE run_id = ?", (run_id,)).fetchone()
        if row is not None:
            cols = list(row.keys())  # sqlite3.Row iterates VALUES, not keys -> keep .keys() explicit
            run_row = {k: row[k] for k in cols}

    resolved = report.get("resolved") if report else bool(run_row.get("resolved"))
    badge = _RESOLVED_BADGE.get(1 if resolved else 0, "⚪ —")
    solver = (report or {}).get("solver") or run_row.get("solver", "?")
    header = (
        f"## {badge}\n"
        f"- **run**: `{run_id}`  ·  **bundle**: `{bundle_root(db).name}`\n"
        f"- **solver**: `{solver}`  ·  **model**: `{run_row.get('model') or '—'}`\n"
        f"- **image_digest**: `{(report or {}).get('image_digest') or run_row.get('image_digest') or '—'}`\n"
        f"- **config_hash**: `{(report or {}).get('config_hash') or '—'}`  ·  "
        f"**deps_hash**: `{(report or {}).get('deps_hash') or '—'}`\n"
        f"- **cost**: `{_cost_summary(run_row.get('cost_json'))}`"
    )

    buckets_md = _buckets_markdown(report) if report else "_(no run.json; showing DB rows only)_"

    rows: list[list[str]] = []
    if report and report.get("per_test"):
        graded = set(_bundle_buckets(db)["fail2pass"]) | set(_bundle_buckets(db)["pass2pass"])
        f2p = set(_bundle_buckets(db)["fail2pass"])
        for tid, outcome in report["per_test"].items():
            bucket = "fail2pass" if tid in f2p else ("pass2pass" if tid in graded else "other")
            rows.append([bucket, str(outcome), tid])
    else:
        rows = [[b, o, t] for b, o, t in load_test_results(db, run_id)]

    patch = _read_text(artifacts_dir(db, run_id) / "solver.patch") or "_(no patch — noop solver or none produced)_"
    return header, buckets_md, rows, patch, _isolation_proof(db, run_id)


def _buckets_markdown(report: dict[str, Any]) -> str:
    buckets = report.get("buckets", {})
    order = [
        ("fail_to_pass", "fail → pass", "the fix (drives the verdict)"),
        ("pass_to_pass", "pass → pass", "no regression (drives the verdict)"),
        ("fail_to_fail", "fail → fail", "still broken (diagnostic)"),
        ("pass_to_fail", "pass → fail", "newly broken (diagnostic)"),
    ]
    lines = ["| transition | passed | failed | meaning |", "|---|---|---|---|"]
    for key, label, meaning in order:
        grp = buckets.get(key, {})
        lines.append(f"| **{label}** | {len(grp.get('success', []))} | {len(grp.get('failure', []))} | {meaning} |")
    return "\n".join(lines)


def resolve_rates(root: Path, group: str) -> list[list[str]]:
    """Pass@1 grouped by `solver` or `domain`, across every bundle."""
    tally: dict[str, list[int]] = {}
    for db in find_databases(root):
        domain = _bundle_domain(db)
        for rec in load_runs(db):
            if rec.get("resolved") is None:
                continue
            key = str(rec.get("solver")) if group == "solver" else domain
            slot = tally.setdefault(key, [0, 0])
            slot[1] += 1
            slot[0] += 1 if rec["resolved"] else 0
    rows = []
    for key, (won, total) in sorted(tally.items(), key=lambda kv: -kv[1][1]):
        pct = f"{100 * won / total:.0f}%" if total else "—"
        rows.append([key, f"{won}/{total}", pct])
    return rows


# --------------------------------------------------------------------------- gradio UI (lazy import)
def _run_label(rec: dict[str, Any]) -> str:
    badge = "🟢" if rec.get("resolved") else ("🔴" if rec.get("resolved") == 0 else "⚪")
    return f"{badge}  {rec['bundle']}  ·  {rec['run_id']}  ·  {rec.get('solver') or '?'} → {rec.get('verdict') or '?'}"


def build_ui(root: Path) -> Any:
    import gradio as gr

    index: dict[str, dict[str, Any]] = {}

    def overview_rows() -> list[list[str]]:
        return [
            [r["bundle"], r["run_id"], str(r.get("solver") or ""), str(r.get("verdict") or ""), r.get("cost", "")]
            for r in all_runs(root)
        ]

    def labels() -> list[str]:
        recs = all_runs(root)
        index.clear()
        for r in recs:
            index[_run_label(r)] = r
        return list(index)

    def on_pick(label: str) -> tuple[Any, Any, Any, Any, Any]:
        rec = index.get(label)
        if not rec:
            return "Pick a run above.", "", [], "", ""
        header, buckets_md, rows, patch, proof = run_detail(rec["db"], rec["run_id"])
        return header, buckets_md, rows, patch, proof

    def refresh() -> tuple[Any, list[list[str]]]:
        return gr.update(choices=labels()), overview_rows()

    with gr.Blocks(title="Patchglass - Run Explorer", css=_CSS) as demo:  # theme -> launch() in gradio 6
        gr.Markdown(
            "# Patchglass - Run Explorer\n"
            "Read-only view over each bundle's `taskbundle.db` + on-disk `runs/` artifacts. "
            "Nothing here writes; verdicts are produced by the CLI, this only renders them."
        )
        with gr.Tab("Runs"):
            picker = gr.Dropdown(choices=labels(), label="Run", interactive=True)
            refresh_btn = gr.Button("↻ Refresh", size="sm")
            header_md = gr.Markdown()
            with gr.Row():
                with gr.Column(scale=1):
                    buckets_md = gr.Markdown(label="Transition buckets")
                    per_test = gr.Dataframe(
                        headers=["bucket", "outcome", "test_id"], label="Per-test results", wrap=True
                    )
                with gr.Column(scale=1):
                    proof_md = gr.Markdown(label="Hidden-file inventory")
            patch_code = gr.Code(label="solver.patch", language="python")
        with gr.Tab("All runs"):
            overview_df = gr.Dataframe(
                value=overview_rows(), headers=["bundle", "run_id", "solver", "verdict", "cost"], wrap=True
            )
        with gr.Tab("Report — Pass@1"), gr.Row():
            gr.Dataframe(value=resolve_rates(root, "solver"), headers=["solver", "n", "pass@1"], label="by solver")
            gr.Dataframe(value=resolve_rates(root, "domain"), headers=["domain", "n", "pass@1"], label="by domain")

        picker.change(on_pick, picker, [header_md, buckets_md, per_test, patch_code, proof_md])
        refresh_btn.click(refresh, None, [picker, overview_df])
    return demo


def main() -> None:
    parser = argparse.ArgumentParser(description="Read-only Gradio explorer for Task Bundle CLI runs.")
    parser.add_argument("--root", type=Path, default=EXAMPLES_DIR, help="Dir containing bundles (default: examples/).")
    parser.add_argument("--port", type=int, default=7860, help="Server port (default: 7860).")
    parser.add_argument("--share", action="store_true", help="Create a public gradio share link.")
    args = parser.parse_args()
    import gradio as gr  # viz extra; data layer above stays import-free

    runs = all_runs(args.root)
    print(f"[dashboard] {len(find_databases(args.root))} bundle DB(s), {len(runs)} run(s) under {args.root}")
    build_ui(args.root).launch(server_name="127.0.0.1", server_port=args.port, share=args.share, theme=gr.themes.Soft())


if __name__ == "__main__":
    main()
