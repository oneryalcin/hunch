"""Online judging: judge() and ajudge(), traffic logging, and shadow specs."""

import asyncio
import concurrent.futures.thread  # noqa: F401  (see _join_shadows: its exit hook must come first)
import csv
import json
import sqlite3
import sys
import threading
from pathlib import Path

from hunch.answers import conf_of, decide, route
from hunch.execute import aexecute
from hunch.spec import canon, digest, load_project, redact, redaction_rules, upstream
from hunch.store import open_store, store_path, write

_projects: dict[Path, dict] = {}


def roots(project: dict) -> list[str]:
    return [n for n in project["order"] if not upstream(project["nodes"][n])]


def log_traffic(project: dict, names: list[str], row: dict) -> None:
    """Rows judged online in shadow mode, so the candidate can be compared on real traffic (--traffic).
    One entry per distinct row and judgment name; `n` counts repeats."""
    db = open_store(project["nodes"][roots(project)[0]])
    db.execute("""create table if not exists traffic (judgment text, rhash text, row text, n integer,
        first_at text default current_timestamp, last_at text default current_timestamp,
        primary key (judgment, rhash))""")
    rules = redaction_rules(project["nodes"][roots(project)[0]])  # the live spec's redaction covers what is kept
    body = json.dumps({k: redact(canon(v), rules) for k, v in row.items()}, ensure_ascii=False)
    write(db, """insert into traffic (judgment, rhash, row, n) values (?, ?, ?, 1) on conflict do update
                 set n = n + 1, last_at = current_timestamp""", [(n, digest(body)[:16], body) for n in names])


def traffic_source(spec: dict) -> Path:
    """This judgment's logged traffic as a CSV, so every command (diff, test, review) can read it like a source.
    Rows without a key value get one from their content, stable across exports (reviews stay attached)."""
    db = open_store(spec)
    try:
        got = db.execute("select rhash, row from traffic where judgment = ? order by first_at, rhash",
                         (spec["judgment"],)).fetchall()
    except sqlite3.OperationalError:
        got = []
    if not got:
        sys.exit(f"{spec['judgment']}: no logged traffic (judge(..., shadow=...) logs it)")
    out = [{spec["key"]: f"t{h}", **json.loads(r)} for h, r in got]
    path = store_path(spec["_dir"]).parent / "traffic" / f"{spec['judgment']}.csv"
    path.parent.mkdir(exist_ok=True)
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, list(dict.fromkeys(k for r in out for k in r)))
        w.writeheader()
        w.writerows(out)
    return path


_background: set = set()  # shadow candidates still running inside an app's event loop


def _project(path: str | Path) -> dict:
    q = Path(path).resolve()
    return _projects.get(q) or _projects.setdefault(q, load_project(q))


async def _shadow(path: str | Path, fields: dict) -> None:
    try:
        await aexecute(_project(path), rows_in=[fields])
    except (Exception, SystemExit) as e:  # a failing candidate (even one that won't load) never fails the live answer
        print(f"shadow {path}: {e!r}", file=sys.stderr)


def _shadow_names(live: dict, shadow: str | Path) -> list[str]:
    try:
        return [n for n in roots(_project(shadow)) if n not in roots(live)]
    except (Exception, SystemExit) as e:
        print(f"shadow {shadow}: {e!r}", file=sys.stderr)
        return []


async def ajudge(path: str | Path, row: dict | None = None, /, *, node: str | None = None,
                 shadow: str | Path | None = None, log: bool = False, **fields) -> dict | None:
    """Judge one row inside an app: the whole project runs on it, same keys as batch (a row the batch already
    judged is a cache hit; a row judged online is a hit for the next batch). Returns {judgment: {question:
    answer}}; a judgment the row never reached (its where-clause said no) is None. For a one-judgment project,
    or with `node`, returns just that judgment's answers.

    shadow: a candidate project that answers the same row after the live answer is returned (a background task
    in this event loop; never returned, never fails the live answer). Its answers are cached, so
    `hunch diff CANDIDATE --against LIVE --traffic` compares them on real traffic for free.
    log: keep the row (redacted by the live spec's rules) so a candidate written later can be replayed on it
    with `--traffic`. Shadowing implies logging.
    The row is `row` (a dict: use it when a column is named node, shadow or log) and/or keyword fields."""
    fields = {**(row or {}), **fields}
    project = _project(path)
    results = await aexecute(project, rows_in=[fields])
    if shadow or log:
        log_traffic(project, roots(project) + (_shadow_names(project, shadow) if shadow else []), fields)
    if shadow:
        task = asyncio.get_running_loop().create_task(_shadow(shadow, fields))
        _background.add(task)
        task.add_done_callback(_background.discard)
    out: dict[str, dict | None] = {}
    for n in project["order"]:
        res = results[n]
        if "union" in res["spec"]:
            continue
        if not res["rows"]:
            out[n] = None
            continue
        out[n] = {}
        for it in res["items"]:
            a = res["answers"][it["key"]]
            label, conf, margin = decide(a)
            # p: probability of the label (hunch's vocabulary); margin: distance from the decision boundary,
            # |p - 0.5| x 2 for yes/no, top minus runner-up for choices (Pydantic AI's `confidence`)
            out[n][it["qid"]] = {"label": label, "p": conf_of(it, a), "margin": round(margin, 4),
                                 "route": route(it["q"], a, it["path_p"]), "cached": it["key"] in res["hits"]}
    if node:
        return out[node]
    return next(iter(out.values())) if len(out) == 1 else out


_SHADOWS: list[threading.Thread] = []


def _join_shadows() -> None:
    for t in _SHADOWS:
        t.join()


# At exit, Python runs these hooks (last registered first) and only then joins non-daemon threads. One of them,
# concurrent.futures', stops every thread pool, and asyncio resolves hosts through one: a shadow still asking
# failed with "cannot schedule new futures after interpreter shutdown" and its answer was lost. Registered after
# concurrent.futures.thread is imported (at the top of this file), this joins the shadows before that happens.
threading._register_atexit(_join_shadows)


def judge(path: str | Path, row: dict | None = None, /, *, node: str | None = None, shadow: str | Path | None = None,
          log: bool = False, **fields) -> dict | None:
    """Sync ajudge. A shadow candidate runs in a thread after the live answer returns; the thread is not a
    daemon, so a script waits for it at exit and the candidate's answers are recorded."""
    fields = {**(row or {}), **fields}
    out = asyncio.run(ajudge(path, fields, node=node, log=log or bool(shadow)))
    if shadow:  # the row was logged under the live names; add the candidate's own
        extra = _shadow_names(_project(path), shadow)
        if extra:
            log_traffic(_project(path), extra, fields)
        t = threading.Thread(target=lambda: asyncio.run(_shadow(shadow, fields)), name="hunch-shadow")
        _SHADOWS.append(t)
        t.start()
    return out
