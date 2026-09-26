"""hunch as a SQL function: a spec's decisions on every row of a table, inside DuckDB.

    import duckdb, hunch.sql
    con = duckdb.connect("warehouse.duckdb")
    hunch.sql.register(con, "command_guard.yml", max_cost=1.00)
    con.sql("select command from commands where command_guard(request, cwd, description, command).destroys.label = 'yes'")

The function is named after the judgment and takes the columns the spec reads (its state), in order. It returns a
struct of the spec's questions, each {label, p, route}: what `hunch.judge` returns for one row. For a project of
several judgments, a struct of judgments, and a judgment a row never reached (its where-clause said no) is NULL.

DuckDB hands the function up to 2,048 rows at a time; each batch is one hunch run over those rows, so answers come
from the same store as `hunch run` and `judge()` (a row judged before costs nothing, and the next batch run reuses
what SQL asked), requests go out concurrently, and `hunch test` still says how often the answers are right.
`max_cost` is the most the function may be charged in total, across every query on this connection; a query that
would go past it fails, keeping the answers it got, and an explicit cap is the point: `select … from big_table`
asks for every row. A distilled engine (`hunch distill`) answers locally for $0.

Needs the `sql` extra: `uv add "hunch-ai[sql]"`.
"""
import asyncio
import inspect
import threading
from pathlib import Path

from hunch import core

FIELDS = (("label", "VARCHAR"), ("p", "DOUBLE"), ("route", "VARCHAR"))
_lock = threading.Lock()  # DuckDB calls from several threads, and the cap (core.MAX_COST) is global: one batch at a time


class Budget:
    """USD that functions may be charged in total. Pass one to several register() calls (dbt does, for every
    connection) and they all draw from it."""

    def __init__(self, cap: float | None):
        self.cap = self.left = cap


def columns(project: dict) -> list[str]:
    """The source columns a project reads: every judgment's state, less the answers upstream judgments add."""
    made = {q for spec in project["nodes"].values() for q in spec.get("questions") or {}}
    cols = [c for n in project["order"] if "union" not in project["nodes"][n]
            for c in core.state_columns(project["nodes"][n])]
    return [c for c in dict.fromkeys(cols) if c not in made]


def register(con, path: str | Path, *, name: str | None = None, max_cost: "float | Budget | None" = None) -> str:
    """Add the spec (or project folder) at `path` to a DuckDB connection as a function; returns its name.
    max_cost: USD for everything this function asks, on this connection (default: $HUNCH_MAX_COST, else no cap),
    or a Budget shared with other functions."""
    try:
        import duckdb
        import pyarrow as pa
    except ImportError:
        raise SystemExit('hunch.sql needs the sql extra: uv add "hunch-ai[sql]"') from None
    project = core.load_project(Path(path).resolve())
    judged = project["order"]
    cols = columns(project)
    key = project["nodes"][core.roots(project)[0]]["key"]
    one = len(judged) == 1
    fname = name or (judged[0] if one else Path(path).resolve().name)

    def qtype(n):  # the SQL and Arrow types of one judgment's answers
        spec = project["nodes"][n]
        qs = [spec["question"]] if "union" in spec else list(spec["questions"])  # a union: its branches' answer
        sql = duckdb.struct_type({q: duckdb.struct_type(dict(FIELDS)) for q in qs})
        arrow = pa.struct([(q, pa.struct([("label", pa.string()), ("p", pa.float64()), ("route", pa.string())])) for q in qs])
        return sql, arrow
    types = {n: qtype(n) for n in judged}
    sql_type = types[judged[0]][0] if one else duckdb.struct_type({n: types[n][0] for n in judged})
    arrow_type = types[judged[0]][1] if one else pa.struct([(n, types[n][1]) for n in judged])
    budget = max_cost if isinstance(max_cost, Budget) else Budget(core.MAX_COST if max_cost is None else max_cost)

    def fn(*arrays):
        # _hunch_row: which row an answer belongs to (ids can repeat, and downstream rows are copies); rows without
        # the spec's key get their position as id, so a union doesn't see one id reaching two branches
        # A NULL column is sent as "", as an empty CSV cell is by `hunch run` (same answer, same cache); a row with
        # every column NULL gets NULL.
        vals = list(zip(*(a.to_pylist() for a in arrays)))
        live = [i for i, v in enumerate(vals) if any(x is not None for x in v)]
        rows = [{**{c: "" if x is None else x for c, x in zip(cols, vals[i])}, "_hunch_row": i} for i in live]
        rows = [r if key in r else {**r, key: str(r["_hunch_row"])} for r in rows]
        with _lock:
            saved, before, core.MAX_COST = core.MAX_COST, core.CHARGED, budget.left
            try:
                results = asyncio.run(core.aexecute(project, rows_in=rows))
            except SystemExit as e:  # the cap, as this function's budget: the engine's message names the CLI flag
                if budget.left is None or "max-cost" not in str(e):
                    raise
                raise SystemExit(f"{fname}: max_cost ${budget.cap} reached; the answers asked so far are saved "
                                 f"(a query over those rows is now free). Register again with a higher max_cost "
                                 f"to ask the rest. ({e})") from None
            finally:
                core.MAX_COST = saved
                if budget.left is not None:  # what was charged, even when the batch stopped at the cap
                    budget.left = max(0.0, budget.left - (core.CHARGED - before))
        out = {n: {} for n in judged}
        for n in judged:
            res = results[n]
            for it in res["items"]:
                a = res["answers"][it["key"]]
                label, _, _ = core.decide(a)
                out[n].setdefault(it["row"]["_hunch_row"], {})[it["qid"]] = {"label": label, "p": core.conf_of(it, a),
                                                             "route": core.route(it["q"], a, it["path_p"])}
        live = set(live)
        got = [None if i not in live else out[judged[0]].get(i) if one else {n: out[n].get(i) for n in judged}
               for i in range(len(vals))]
        return pa.array(got, type=arrow_type)

    # DuckDB counts the function's parameters: one per column, not *arrays
    fn.__signature__ = inspect.Signature([inspect.Parameter(f"c{i}", inspect.Parameter.POSITIONAL_ONLY) for i in range(len(cols))])
    con.create_function(fname, fn, ["VARCHAR"] * len(cols), sql_type, type="arrow", null_handling="special",
                        side_effects=False)
    return fname
