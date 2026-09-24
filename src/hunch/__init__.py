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

from hunch.core import (  # noqa: F401  (the public surface)
    ajudge, decide, execute, judge, lint, load_project, load_spec, spec_yaml, table_name,
)
from hunch.models import spec_from_model, to_model  # noqa: F401

__version__ = "0.1.0"


def load(obj, base: str | Path = ".") -> dict:
    """A project from a spec path, a folder of specs, a spec dict (e.g. from spec_from_model) or a list of spec
    dicts (a graph built in Python: generate the specs, don't copy them). Relative sources resolve against base."""
    from hunch.core import expand_multi, topo_project
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
    from hunch.core import cmd_run
    project = load(obj, base)
    cmd_run(project, argparse.Namespace())
    return project


def results(obj, base: str | Path = ".", judgment: str | None = None) -> list[dict]:
    """The materialized table of a judgment (the last complete run), as a list of dicts."""
    from hunch.core import open_store
    project = load(obj, base)
    spec = project["nodes"][judgment or project["order"][-1]]
    db = open_store(spec)
    cur = db.execute(f'select * from "{table_name(spec)}"')
    cols = [c[0] for c in cur.description]
    return [dict(zip(cols, r)) for r in cur.fetchall()]


def judge_model(cls, spec: dict, base: str | Path = ".", **fields):
    """Judge one row with a spec built from a Pydantic class; returns an instance of that class."""
    import asyncio
    from hunch.core import aexecute
    project = load(spec, base)
    res = asyncio.run(aexecute(project, rows_in=[fields]))[project["order"][0]]
    answers = {it["qid"]: {"label": decide(res["answers"][it["key"]])[0]} for it in res["items"]}
    return to_model(cls, answers)  # a bare output type (Literal, bool, ...) returns the value itself
