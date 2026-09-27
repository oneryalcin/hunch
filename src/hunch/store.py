"""The answer store and what a run leaves behind: tables, lineage, run records, results and receipt paths."""

import json
import os
import sqlite3
import subprocess
import sys
import threading
import time
from pathlib import Path

from hunch import settings
from hunch.spec import META_KEYS, answer_columns, digest, source_header, upstream

_conns: dict[tuple[Path, int], sqlite3.Connection] = {}


def store_path(start: Path) -> Path:
    if settings.STORE is not None:
        return settings.STORE
    if os.environ.get("HUNCH_STORE"):
        return Path(os.environ["HUNCH_STORE"]).resolve()
    start = start.resolve()
    for d in [start, *start.parents]:
        if (d / ".hunch" / "store.sqlite").exists():
            return d / ".hunch" / "store.sqlite"
    root = next((d for d in [start, *start.parents] if (d / ".git").exists()), start)
    return root / ".hunch" / "store.sqlite"  # new stores go at the repo root, so sibling projects share one


def open_store(spec: dict) -> sqlite3.Connection:
    """SQLite in WAL mode: many processes (batch runs, apps calling judge()) can share it.
    One connection per thread and store; writes are short explicit transactions.
    One store per workspace: $HUNCH_STORE, else the nearest `.hunch/store.sqlite` in this folder or above,
    else a new one here. Answers are content-addressed facts; a store per folder made comparisons across
    projects pay twice."""
    path = spec.get("_store") or store_path(spec["_dir"])  # _store: the store a judge() target names
    if (path, threading.get_ident()) in _conns:  # one connection per thread: WAL lets them share the file
        return _conns[(path, threading.get_ident())]
    if not path.exists() and settings.VERBOSE:  # the command summary shows the chosen store; verbose explains why it is new
        print(f"new answer store: {path} (HUNCH_STORE=... to share one)", file=sys.stderr)
    path.parent.mkdir(exist_ok=True)
    db = sqlite3.connect(path, timeout=30, isolation_level=None, check_same_thread=False)
    for _ in range(100):  # switching to WAL needs a moment alone with the file; it persists once set
        try:
            if db.execute("pragma journal_mode").fetchone()[0] != "wal":
                db.execute("pragma journal_mode=wal")
            break
        except sqlite3.OperationalError:
            time.sleep(0.05)
    db.execute("pragma synchronous=normal")
    db.execute("""create table if not exists answers (
        key text primary key, model text, answer text, input_tokens real,
        created_at text default current_timestamp)""")
    _conns[(path, threading.get_ident())] = db
    return db


def write(db: sqlite3.Connection, sql: str, rows: list) -> None:
    db.execute("begin immediate")
    try:
        db.executemany(sql, rows)
        db.execute("commit")
    except BaseException:
        db.execute("rollback")
        raise


def cached(db, keys: list[str]) -> dict[str, dict]:
    out = {}
    for i in range(0, len(keys), 500):
        chunk = keys[i:i + 500]
        q = f"select key, answer from answers where key in ({','.join('?' * len(chunk))})"
        out |= {k: json.loads(a) for k, a in db.execute(q, chunk)}
    return out


def previous_keys(db, judgment: str) -> dict[tuple[str, str], tuple[str, str]]:
    row_answers_table(db)
    return {(r, q): (k, s) for r, q, k, s in db.execute(  # run ids start with their time: the last one wins
        "select row_id, qid, key, shash from _hunch_row_answers where judgment = ? order by run_id", (judgment,))}


def output_columns(project: dict, results: dict, name: str) -> list[str]:
    """A judgment's output columns: from its rows, or, when it kept none, from its input and questions."""
    if results[name]["rows"]:
        return list(dict.fromkeys(c for r in results[name]["rows"] for c in r))
    spec, ups = project["nodes"][name], upstream(project["nodes"][name])
    if "union" in spec:
        return list(dict.fromkeys([c for u in ups for c in output_columns(project, results, u)] + ["_branch"]))
    inp = output_columns(project, results, ups[0]) if ups else source_header(spec) + (["_w"] if "weights" in spec else [])
    multi = list(spec.get("_multi", {}))  # rows put a multi question's combined set after its parts
    return list(dict.fromkeys(inp + ["_path_p"] + [c for c in answer_columns(spec) if c not in multi] + multi))


def materialize(spec: dict, db, out_rows: list[dict], run_id: str = "", keys: dict | None = None,
                columns: list[str] | None = None) -> None:
    """The judgment's table: its input columns plus its answers, like a dbt model's select *, plus lineage:
    `_hunch_run_id` and, per question, `<qid>_key` (the content address of the answer in the store).
    `columns` are used when there are no rows, so a filter that keeps nothing still leaves a (empty) table.
    Replaced in one transaction, so a reader sees the previous complete run or this one, never a mix."""
    lineage = []
    if keys is not None:
        out_rows = [{**r, "_hunch_run_id": run_id, **keys.get(i, {})} for i, r in enumerate(out_rows)]
        lineage = ["_hunch_run_id", *(f"{q}_key" for q in spec["questions"])]
    cols = list(dict.fromkeys(c for r in out_rows for c in r)) or [*(columns or []), *lineage]
    typed = ", ".join(f'"{c}" {"real" if c.endswith(("_p", "_pyes")) else "text"}' for c in cols)
    table = table_name(spec)
    db.execute("begin immediate")
    try:
        db.execute(f'drop table if exists "{table}"')
        db.execute(f'create table "{table}" ({typed})')
        db.executemany(f'insert into "{table}" values ({", ".join("?" * len(cols))})', [[r.get(c) for c in cols] for r in out_rows])
        db.execute("commit")
    except BaseException:
        db.execute("rollback")
        raise


def record_run(db, run: dict) -> None:
    db.execute("""create table if not exists _hunch_runs (run_id text, judgment text, spec_hash text, git_sha text,
        model text, rows integer, asked integer, cost real, status text, started_at text, finished_at text)""")
    write(db, "insert into _hunch_runs values (:run_id, :judgment, :spec_hash, :git_sha, :model, :rows, :asked, :cost, "
              ":status, :started_at, :finished_at)", [run])


def row_answers_table(db) -> None:
    """One entry per row, question and run: the history drift checks compare; on_change reads each row's latest.
    Stores from before `shash` existed get the column (their old rows can't be matched, so they are re-asked)."""
    db.execute("""create table if not exists _hunch_row_answers (judgment text, row_id text, qid text, key text,
        shash text, run_id text, primary key (judgment, row_id, qid, run_id))""")
    if "shash" not in {r[1] for r in db.execute("pragma table_info(_hunch_row_answers)")}:
        db.execute("alter table _hunch_row_answers add column shash text")


def record_row_answers(db, judgment: str, items: list[dict], run_id: str) -> None:
    row_answers_table(db)
    write(db, "insert or replace into _hunch_row_answers (judgment, row_id, qid, key, shash, run_id) "
              "values (?, ?, ?, ?, ?, ?)", [(judgment, it["id"], it["qid"], it["key"], it["shash"], run_id) for it in items])


def git_sha(path: Path) -> str:
    try:
        return subprocess.run(["git", "-C", str(path), "rev-parse", "--short", "HEAD"], capture_output=True,
                              text=True, timeout=5).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return ""


def frozen_changes(project: dict) -> list[str]:
    """on_change: freeze: judgments whose spec differs from their last complete run."""
    out = []
    for n in project["order"]:
        spec = project["nodes"][n]
        if spec.get("on_change") != "freeze":
            continue
        try:
            last = open_store(spec).execute("select spec_hash from _hunch_runs where judgment = ? and status = 'complete' "
                                            "order by finished_at desc limit 1", (table_name(spec),)).fetchone()
        except sqlite3.OperationalError:
            last = None
        if last and last[0] != spec_hash(spec):
            out.append(n)
    return out


def table_name(spec: dict) -> str:
    """The judgment's identity in the store: its name, plus the engine when run with --model."""
    return spec["judgment"] + spec.get("_table_suffix", "")


def spec_hash(spec: dict) -> str:
    """What the judgment asks: not its policy (on_change) or where this run's rows come from (source, which
    --source and --traffic replace) or how other targets would run it, so freeze guards the questions, not the data."""
    return digest({k: v for k, v in spec.items() if not k.startswith("_") and k not in ("on_change", "source", "targets", *META_KEYS)})[:12]


def results_path(project: dict) -> Path:
    """.hunch/target/<tested spec or folder, relative to the store's folder>.json: one file per spec or project, so
    projects sharing a store keep their own; with --model, the engine's suffix too (`triage__deepseek_deepseek_flash`);
    with --target, its name (`triage@dev`), so a dev test never replaces the results docs and CI read."""
    spec = project["nodes"][project["order"][0]]
    store = store_path(spec["_dir"])
    tested = Path(project["path"]).resolve()
    try:
        name = tested.relative_to(store.parent.parent).with_suffix("")
    except ValueError:  # tested outside the store's folder (HUNCH_STORE elsewhere)
        name = Path(tested.stem)
    suffix = spec.get("_table_suffix", "")
    suffix += f"@{settings.TARGET}" if settings.TARGET and not suffix.endswith(f"@{settings.TARGET}") else ""
    return store.parent / "target" / name.with_name(name.name + suffix).with_suffix(".json")


def receipt_path(project: dict) -> Path:
    """Beside what was tested: `<spec>.results.json`, or `results.json` in a project folder; under --model with the
    engine's suffix, as its tables have (`answer_support.results__ollama_qwen2_5_0_5b.json`)."""
    tested = Path(project["path"]).resolve()
    suffix = project["nodes"][project["order"][0]].get("_table_suffix", "")
    return (tested / f"results{suffix}.json") if tested.is_dir() else tested.with_name(f"{tested.stem}.results{suffix}.json")
