"""hunch: judgments as code. Write a question once, test it against gold, diff every change, review what matters,
and choose how much to automate by looking at the dial.

Library first; the `hunch` CLI is a thin shell over the same functions.

    import hunch
    hunch.judge("triage.yml", subject="Charged twice", body="...")      # online, cached, same keys as batch
    results = hunch.run("triage.yml")                                     # batch: every judgment, in order
    rows = hunch.results("triage.yml")                                    # the materialized table, as dicts

    # Pydantic classes as specs (the same mapping as Pydantic AI's TypeSafe model)
    spec = hunch.spec_from_model(Triage, source="tickets.csv", state=["subject", "body"])
    ticket = hunch.judge_model(Triage, spec, subject="...", body="...")   # a Triage instance
"""
from pathlib import Path

# the public surface
from hunch.answers import decide  # noqa: F401
from hunch.execute import execute  # noqa: F401
from hunch.lint import lint  # noqa: F401
from hunch.models import spec_from_agent, spec_from_model, to_model  # noqa: F401
from hunch.online import ajudge, judge  # noqa: F401
from hunch.spec import load_project, load_spec  # noqa: F401
from hunch.store import table_name  # noqa: F401
from hunch.suggest import spec_yaml  # noqa: F401

__version__ = "0.3.1"


def load(obj, base: str | Path = ".") -> dict:
    """A project from a spec path, a folder of specs, a spec dict (e.g. from spec_from_model) or a list of spec
    dicts (a graph built in Python: generate the specs, don't copy them). Relative sources resolve against base."""
    from hunch.spec import expand_multi, topo_project
    if isinstance(obj, (dict, list)):
        specs = []
        for s in obj if isinstance(obj, list) else [obj]:
            spec = {**s, "_dir": Path(base).resolve()}
            expand_multi(spec)
            specs.append(spec)
        return topo_project(specs)
    return load_project(Path(obj))


def run(obj, base: str | Path = ".") -> dict:
    """Run every judgment (asks only what the store lacks) and materialize its table; returns the results."""
    import argparse

    from hunch.commands import cmd_run
    project = load(obj, base)
    cmd_run(project, argparse.Namespace())
    return project


def results(obj, base: str | Path = ".", judgment: str | None = None) -> list[dict]:
    """The materialized table of a judgment (the last complete run), as a list of dicts. Refuses a table another
    version of the spec wrote, or another spec with the same judgment name: its answers aren't this spec's."""
    from hunch.store import open_store, spec_hash
    project = load(obj, base)
    spec = project["nodes"][judgment or project["order"][-1]]
    db, name = open_store(spec), table_name(spec)
    if not db.execute("select 1 from sqlite_master where type = 'table' and name = ?", (name,)).fetchone():
        raise SystemExit(f"{name}: no table yet; `hunch run {project['path']}` writes it")
    cur = db.execute(f'select * from "{name}"')
    cols = [c[0] for c in cur.description]
    rows = [dict(zip(cols, r)) for r in cur.fetchall()]
    run = rows and rows[0].get("_hunch_run_id") and db.execute(
        "select spec_hash, finished_at from _hunch_runs where run_id = ? and judgment = ?",
        (rows[0]["_hunch_run_id"], name)).fetchone()
    if run and run[0] != spec_hash(spec):
        raise SystemExit(f"{name}: the table was written at {run[1]} by another version of this spec, or by another "
                         f"spec with the same judgment name; `hunch run {project['path']}` writes it for this one "
                         "(free when the answers are cached)")
    return rows


def judge_model(cls, spec: dict, base: str | Path = ".", **fields):
    """Judge one row with a spec built from a Pydantic class; returns an instance of that class."""
    import asyncio

    from hunch.execute import aexecute
    project = load(spec, base)
    res = asyncio.run(aexecute(project, rows_in=[fields]))[project["order"][0]]
    answers = {it["qid"]: {"label": decide(res["answers"][it["key"]])[0]} for it in res["items"]}
    return to_model(cls, answers)  # a bare output type (Literal, bool, ...) returns the value itself
