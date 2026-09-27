"""Running a project: each judgment's rows, answers and escalations, in dependency order."""

import asyncio
import hashlib
import sys
from collections import Counter

from hunch import settings
from hunch.answers import conf_of, decide, p_where, route
from hunch.fill import fill, merge_stats
from hunch.spec import Unknown, compile_where, item, plan, rows, upstream
from hunch.store import cached, open_store, previous_keys, table_name

ZERO = {"cached": 0, "asked": 0, "requests": 0, "tokens": 0, "cost": 0.0}


async def aexecute(project: dict, rows_in: list[dict] | None = None, dry: bool = False, hold: bool = False) -> dict[str, dict]:
    """Run every judgment in dependency order. A judgment's input rows are its source file, `rows_in` (online),
    or its upstream judgment's output rows (every input column plus the upstream answers). `where` drops rows
    before anything is asked. `dry` asks nothing: missing answers are reported, and rows whose where-clause
    depends on a missing answer are kept (an upper bound)."""
    results: dict[str, dict] = {}
    for name in project["order"]:
        spec, ups = project["nodes"][name], upstream(project["nodes"][name])
        if "union" in spec:
            parts = [results[u] for u in ups]
            items = [it for res in parts for it in res["items"] if it["qid"] == spec["question"]]
            dupes = [i for i, c in Counter(it["id"] for it in items).items() if c > 1]
            if dupes and not dry:  # a dry run keeps rows with unknown answers in every branch (upper bound)
                sys.exit(f"{name}: rows {dupes[:5]} reach more than one branch; a union needs branches whose where-clauses don't overlap")
            results[name] = {"spec": spec, "rows": [r | {"_branch": u} for u, res in zip(ups, parts) for r in res["rows"]],
                             "items": items, "answers": {k: v for res in parts for k, v in res["answers"].items()},
                             "stats": dict(ZERO), "input": sum(len(res["rows"]) for res in parts), "unknown": 0, "hits": set()}
            continue
        inp = results[ups[0]]["rows"] if ups else (rows_in if rows_in is not None else rows(spec))
        if not ups and "weights" in spec and rows_in is None:
            inp = weigh(inp, spec["weights"])
        if not ups and rows_in is None and settings.SAMPLE is not None:
            inp = sorted(inp, key=lambda r: hashlib.sha256(str(r.get(spec["key"], "")).encode()).hexdigest())[:settings.SAMPLE]
        keep, unknown, known, passed, unknown_rows = inp, 0, 0, 0, []
        if "where" in spec:
            pred, _ = compile_where(spec["where"])
            keep = []
            for r in inp:
                try:
                    ok = pred(r)
                    known, passed = known + 1, passed + ok
                except Unknown:  # only a dry run has missing answers: keep the row (upper bound)
                    ok, unknown = dry, unknown + dry
                    unknown_rows.append(r)
                if ok:
                    keep.append(r)
            if inp and not keep and rows_in is None:  # a batch keeping nothing is likely a typo or a type mismatch
                print(f"{name}: warning: where kept 0 of {len(inp)} rows: {spec['where']}", file=sys.stderr)
        path_ps = [1.0] * len(keep)
        if spec.get("chain"):  # once per hop: P(this hop's where-clause) × the upstream row's own path
            pred, used = compile_where(spec["where"])
            up = results[ups[0]]
            reads = used & set(up["spec"].get("questions", {}))
            by_row: dict[str, dict] = {}
            for it in up["items"]:
                if it["qid"] in reads:
                    by_row.setdefault(it["id"], {})[it["qid"]] = up["answers"].get(it["key"])
            key_col = up["spec"]["key"]
            path_ps = [r.get("_path_p", 1.0) * p_where(pred, r, by_row.get(str(r.get(key_col)), {})) for r in keep]
        if rows_in is None and not ups:
            dup = [i for i, c in Counter(str(r.get(spec["key"])) for r in keep).items() if c > 1]
            if dup:
                print(f"{name}: warning: {len(dup)} key values repeat (e.g. {dup[:3]}); rows are paired by key in "
                      f"diff, review and on_change, so make `{spec['key']}` unique", file=sys.stderr)
        items = plan(spec, keep)
        if hold:
            hold_answers(spec, open_store(spec), items)
        nq = len(spec["questions"])
        for i, it in enumerate(items):
            it["path_p"] = path_ps[i // nq]
        db = open_store(spec)
        if dry:
            answers = cached(db, [it["key"] for it in items])
            hits, stats = set(answers), {**ZERO, "cached": len(answers)}
        else:
            hits = set(cached(db, [it["key"] for it in items]))
            answers, stats = await fill(spec, db, items)
        answers, by, stats = await escalate(spec, db, items, answers, stats, dry, hits)
        out_rows = []
        for i, r in enumerate(keep):
            out = dict(r)
            out["_path_p"] = round(path_ps[i], 4)
            for it in items[i * nq:(i + 1) * nq]:
                a, qid = answers.get(it["key"]), it["qid"]
                label = decide(a)[0] if a else None
                out[qid], out[f"{qid}_p"] = label, round(conf_of(it, a), 3) if a else None
                if it["q"]["type"] == "noul":
                    out[f"{qid}_pyes"] = round(a["noul"], 3) if a else None
                if "act" in it["q"]:
                    out[f"{qid}_route"] = route(it["q"], a, it["path_p"]) if a else None
                if "escalate" in it["q"]:
                    out[f"{qid}_by"] = by.get(it["key"], spec["model"]) if a else None
            for parent, labels in spec.get("_multi", {}).items():
                out[parent] = "|".join(lab for lab in labels if out.get(f"{parent}__{lab}") == "yes")
            out_rows.append(out)
        # rows the where-clause is expected to keep, from the pass rate of rows whose answers are known
        expected = passed + unknown * (passed / known) if known else None
        results[name] = {"spec": spec, "rows": out_rows, "items": items, "answers": answers, "stats": stats,
                         "input": len(inp), "unknown": unknown, "hits": hits, "escalated": by,
                         "expected": expected if unknown else None, "pass_rate": passed / known if known else None,
                         "unknown_ids": {str(r.get(spec.get("key"))) for r in unknown_rows}}
    return results


async def escalate(spec: dict, db, items: list[dict], answers: dict, stats: dict, dry: bool,
                   hits: set) -> tuple[dict, dict, dict]:
    """escalate: {model: X} on a question re-asks, on engine X, only the answers that fall below `act`; the
    escalated answer replaces the original when it clears `act` itself. Behind a distilled student it always does:
    the teacher is the better model, and a student's confidence is not comparable with its teacher's. Both stay in the store; the returned
    answers are the combined system's, `by` says which key was answered by which engine. Escalated answers that
    were already stored are added to `hits`."""
    todo: dict[str, list[dict]] = {}
    for it in items:
        a = answers.get(it["key"])
        if "escalate" in it["q"] and a and route(it["q"], a, it.get("path_p", 1.0)) == "review":
            todo.setdefault(it["q"]["escalate"]["model"], []).append(it)
    if not todo:
        return answers, {}, stats
    final, by = dict(answers), {}
    for model, its in todo.items():
        espec = {**spec, "model": model}
        eitems = [item(espec, it["row"], it["qid"]) for it in its]
        hits |= set(cached(db, [e["key"] for e in eitems]))
        if dry:
            got, estats = cached(db, [e["key"] for e in eitems]), {**ZERO}
        else:
            got, estats = await fill(espec, db, eitems)
        stats = merge_stats(stats, estats)
        for it, e in zip(its, eitems):
            ea = got.get(e["key"])
            if ea and (route(it["q"], ea, it.get("path_p", 1.0)) == "act" or spec["model"].startswith("distilled:")):
                it["key"] = e["key"]  # the row's answer is now the escalated one (lineage, drift and tests follow)
                final[e["key"]], by[e["key"]] = ea, model
    return final, by, stats


def hold_answers(spec: dict, db, items: list[dict]) -> None:
    """on_change: new_rows_only: after a spec edit, a row answered before keeps the answer it got, as long as its
    input is unchanged (same id and same state; edited text is a new row); only new rows are asked. Applied by
    `run` and `compile` (what run will cost); test, diff, review and judge always see the spec as written.
    (freeze is checked before a run; reask, the default, asks every row under the new spec.) Rewrites keys."""
    if spec.get("on_change", "reask") != "new_rows_only":
        return set()
    prev = previous_keys(db, table_name(spec))
    for it in items:
        old = prev.get((it["id"], it["qid"]))
        if old and old[1] == it["shash"]:
            it["key"] = old[0]


def weigh(rs: list[dict], wt: dict) -> list[dict]:
    """Sampling weights: the source over- or under-samples some kind of row (e.g. a 50/50 eval set of a population
    that is 17% "yes"). Each row gets `_w` = population share / sample share of its value in `by`. Rows carry the
    weight downstream, so every judgment is scored on its own sub-population: runs that claim a fix pass ~24% of
    the time, not 17%, and a per-question base rate could not know that."""
    counts = Counter(str(r[wt["by"]]) for r in rs)
    pop = {str(k): v for k, v in wt["population"].items()}
    missing = set(counts) - set(pop)
    if missing:
        sys.exit(f"weights: values {sorted(missing)} of {wt['by']!r} have no population share")
    return [r | {"_w": pop[str(r[wt["by"]])] / (counts[str(r[wt["by"]])] / len(rs))} for r in rs]


def execute(project: dict, **kw) -> dict[str, dict]:
    return asyncio.run(aexecute(project, **kw))


def pick(project: dict, name: str | None) -> str:
    if name:
        if name not in project["nodes"]:
            sys.exit(f"no judgment {name!r}; have {project['order']}")
        return name
    if len(project["nodes"]) == 1:
        return project["order"][0]
    sys.exit(f"this project has several judgments; pick one with --node ({', '.join(project['order'])})")


def print_stats(s: dict, prefix: str = "answers") -> None:
    print(f"{prefix}: {s['cached']} cached, {s['asked']} asked in {s['requests']} requests, "
          f"{s['tokens']:,} input tokens, ${s['cost']:.5f}")


def with_upstream(project: dict, name: str) -> dict:
    """The part of a project that `name` needs: it and every judgment it reads from (dbt's +model)."""
    need, todo = set(), [name]
    while todo:
        n = todo.pop()
        if n not in need:
            need.add(n)
            todo += upstream(project["nodes"][n])
    return {**project, "nodes": {n: s for n, s in project["nodes"].items() if n in need},
            "order": [n for n in project["order"] if n in need]}
