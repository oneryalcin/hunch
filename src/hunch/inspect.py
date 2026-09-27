"""Read-only inspection of the last materialized hunch table."""

from __future__ import annotations

import os
import re
import shutil
import sqlite3
import sys
import textwrap
from pathlib import Path
from urllib.parse import quote

from hunch import core


def show(project: dict, args) -> None:
    """Print the rows already materialized by `hunch run`, without reading sources or asking models."""
    _apply_model_suffix(project, getattr(args, "model", None))
    node = core.pick(project, getattr(args, "node", None))
    spec = project["nodes"][node]
    if "union" in spec:
        keys = {project["nodes"][branch].get("key") for branch in spec["union"]}
        if len(keys) != 1 or None in keys:
            sys.exit(f"{node}: union branches use different keys; inspect a branch with --node")
        spec = {**spec, "key": keys.pop()}
    table = core.table_name(spec)
    path = core.store_path(spec["_dir"])
    if not path.exists():
        sys.exit(f"{_rel(path)}: no store yet; `hunch run {project['path']}` writes it")

    db = _connect_readonly(path)
    if not _has_table(db, table):
        sys.exit(f"{table}: no table yet; `hunch run {project['path']}` writes it")
    cols = _columns(db, table)
    if spec.get("key") not in cols:
        sys.exit(f"{table}: key column {spec.get('key')!r} is not in the materialized table")

    run = _run_for_table(db, table, cols)
    print(_heading(node, table, path, run, spec))
    if run and run.get("spec_hash") and run["spec_hash"] != core.spec_hash(spec):
        print(f"warning: table was written by spec {run['spec_hash'][:12]}; current spec is {core.spec_hash(spec)[:12]}")

    row_id = getattr(args, "id", None)
    if row_id is not None:
        _print_one(db, table, cols, spec["key"], str(row_id))
        return

    raw_limit = getattr(args, "limit", None)
    limit = 20 if raw_limit is None else int(raw_limit)
    if limit < 1:
        sys.exit("--limit must be a positive number")
    rows = _rows(db, table, cols, spec["key"], limit)
    if not rows:
        print("no rows in this table")
        return
    _print_table(rows, spec, cols)
    total = _count(db, table)
    if total > len(rows):
        print(f"... {total - len(rows)} more row(s); use --limit {total} or --id <{spec['key']}>")


def _apply_model_suffix(project: dict, model: str | None) -> None:
    """Let direct callers inspect `--model` tables; CLI wiring may already have applied this."""
    if not model:
        return
    suffix = "__" + re.sub(r"\W+", "_", model).strip("_")
    for spec in project["nodes"].values():
        if spec.get("_table_suffix") == suffix:
            continue
        if not spec.get("_table_suffix"):
            spec["_table_suffix"] = suffix
        if "union" not in spec:
            spec["model"] = model


def _connect_readonly(path: Path) -> sqlite3.Connection:
    uri = "file:" + quote(str(path.resolve())) + "?mode=ro"
    try:
        return sqlite3.connect(uri, uri=True)
    except sqlite3.OperationalError as e:
        sys.exit(f"{_rel(path)}: cannot open store read-only ({e})")


def _has_table(db: sqlite3.Connection, table: str) -> bool:
    return bool(db.execute("select 1 from sqlite_master where type = 'table' and name = ?", (table,)).fetchone())


def _columns(db: sqlite3.Connection, table: str) -> list[str]:
    if not _has_table(db, table):
        sys.exit(f"{table}: no table in store")
    return [r[1] for r in db.execute(f"pragma table_info({_q(table)})")]


def _run_for_table(db: sqlite3.Connection, table: str, table_cols: list[str]) -> dict | None:
    if not _has_table(db, "_hunch_runs"):
        return None
    cols = [r[1] for r in db.execute('pragma table_info("_hunch_runs")')]
    need = {"run_id", "judgment", "spec_hash", "model", "rows", "status", "finished_at"}
    if not need <= set(cols):
        return None
    if "_hunch_run_id" in table_cols:
        run_id = db.execute(f"select _hunch_run_id from {_q(table)} where _hunch_run_id is not null limit 1").fetchone()
        if run_id:
            row = db.execute('select run_id, spec_hash, model, rows, status, finished_at from "_hunch_runs" '
                             "where judgment = ? and run_id = ?", (table, run_id[0])).fetchone()
            if row:
                return dict(zip(["run_id", "spec_hash", "model", "rows", "status", "finished_at"], row))
    row = db.execute('select run_id, spec_hash, model, rows, status, finished_at from "_hunch_runs" '
                     "where judgment = ? and status = 'complete' order by finished_at desc, run_id desc limit 1",
                     (table,)).fetchone()
    if not row:
        return None
    return dict(zip(["run_id", "spec_hash", "model", "rows", "status", "finished_at"], row))


def _heading(node: str, table: str, path: Path, run: dict | None, spec: dict) -> str:
    lines = [f"{node} · last materialized run", f"  Table: {table}", f"  Store: {_rel(path)}"]
    if run:
        lines.append(f"  Run: {run['run_id']}")
        lines.append(f"  Rows: {run['rows']} · finished {run['finished_at']}")
        if run.get("model"):
            lines.append(f"  Model: {run['model']}")
    else:
        lines.append("  Run metadata not found")
        if spec.get("model"):
            lines.append(f"  Current model: {spec['model']}")
    return "\n".join(lines)


def _rows(db: sqlite3.Connection, table: str, cols: list[str], key: str, limit: int) -> list[dict]:
    return _dicts(db.execute(f"select * from {_q_existing(cols, table, table)} order by {_q_existing(cols, key, table)} limit ?",
                             (limit,)))


def _count(db: sqlite3.Connection, table: str) -> int:
    return int(db.execute(f"select count(*) from {_q(table)}").fetchone()[0])


def _print_table(rows: list[dict], spec: dict, cols: list[str]) -> None:
    qs = [q for q in core.question_of(spec) if q in cols]
    headers = [spec["key"], *qs]
    if "_branch" in cols:
        headers.append("_branch")
    data = []
    for row in rows:
        line = [_brief(row.get(spec["key"]))]
        for qid in qs:
            line.append(_answer_cell(row, qid, cols))
        if "_branch" in cols:
            line.append(_brief(row.get("_branch")))
        data.append(line)
    _render_table(headers, data)


def _answer_cell(row: dict, qid: str, cols: list[str]) -> str:
    parts = [_brief(row.get(qid))]
    if f"{qid}_p" in cols and row.get(f"{qid}_p") not in (None, ""):
        parts.append(f"{float(row[f'{qid}_p']):.2f}")
    if f"{qid}_route" in cols and row.get(f"{qid}_route"):
        parts.append(str(row[f"{qid}_route"]))
    if f"{qid}_by" in cols and row.get(f"{qid}_by"):
        parts.append(str(row[f"{qid}_by"]))
    return " ".join(parts)


def _print_one(db: sqlite3.Connection, table: str, cols: list[str], key: str, row_id: str) -> None:
    if key not in cols:
        sys.exit(f"{table}: key column {key!r} is not in the materialized table")
    rows = _dicts(db.execute(f"select * from {_q(table)} where {_q(key)} = ? order by rowid", (row_id,)))
    if not rows:
        sys.exit(f"{table}: no row where {key} = {row_id!r}")
    width = _width()
    for i, row in enumerate(rows):
        if len(rows) > 1:
            print(f"row {i + 1}/{len(rows)}")
        for col in cols:
            label = f"{col}: "
            text = _clean(row.get(col))
            wrapped = textwrap.wrap(text, width=max(20, width - len(label)), replace_whitespace=False,
                                    drop_whitespace=False) or [""]
            print(label + wrapped[0])
            for extra in wrapped[1:]:
                print(" " * len(label) + extra)


def _render_table(headers: list[str], rows: list[list[str]]) -> None:
    width = _width()
    max_cell = max(12, min(32, width // max(2, len(headers)) - 3))
    data = [[_clip(h, max_cell) for h in headers], *[[_clip(c, max_cell) for c in row] for row in rows]]
    widths = [max(len(row[i]) for row in data) for i in range(len(headers))]
    if sum(widths) + 2 * (len(headers) - 1) > width:
        for row in rows:
            print(f"{headers[0]}: {row[0]}")
            for heading, value in zip(headers[1:], row[1:]):
                print(f"  {heading}: {_clip(value, max(12, width - len(heading) - 4))}")
        return
    for i, row in enumerate(data):
        print("  ".join(cell.ljust(widths[j]) for j, cell in enumerate(row)))
        if i == 0:
            print("  ".join("-" * w for w in widths))


def _dicts(cur: sqlite3.Cursor) -> list[dict]:
    cols = [c[0] for c in cur.description]
    return [dict(zip(cols, row)) for row in cur.fetchall()]


def _q_existing(cols: list[str], name: str, table: str) -> str:
    if name != table and name not in cols:
        sys.exit(f"{table}: no column {name!r}")
    return _q(name)


def _q(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def _brief(value) -> str:
    return _clean(value).replace("\\n", " ")


def _clean(value) -> str:
    if value is None:
        return ""
    text = str(value)
    out = []
    for ch in text:
        code = ord(ch)
        if ch == "\n":
            out.append("\\n")
        elif ch == "\t":
            out.append("\\t")
        elif code < 32 or code == 127:
            out.append(f"\\x{code:02x}")
        else:
            out.append(ch)
    return "".join(out)


def _clip(value: str, width: int) -> str:
    return value if len(value) <= width else value[: max(1, width - 1)] + "…"


def _width() -> int:
    return shutil.get_terminal_size((100, 24)).columns


def _rel(path: Path) -> str:
    try:
        return os.path.relpath(path, Path.cwd())
    except ValueError:
        return str(path)
