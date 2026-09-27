"""The `hunch` command line: arguments, targets, and lint, compile, run, distill, docs and show."""

import argparse
import json
import os
import re
import sys
import time
from pathlib import Path

from hunch import engines, settings
from hunch.diff import cmd_diff
from hunch.execute import execute, pick, print_stats, with_upstream
from hunch.fill import estimate_cost, llm_prompt, merge_stats, oversized, price_per_token
from hunch.lint import apply_target, lint, lint_targets
from hunch.measure import cmd_test
from hunch.online import roots, traffic_source
from hunch.review import cmd_review
from hunch.spec import detail, digest, is_llm, load_project, question_of, upstream
from hunch.store import (
    frozen_changes,
    git_sha,
    materialize,
    open_store,
    output_columns,
    record_row_answers,
    record_run,
    spec_hash,
    store_path,
    table_name,
)
from hunch.suggest import WRITER, cmd_suggest


def cmd_lint(project: dict, _args) -> None:
    print(f"lint: ok ({len(project['nodes'])} judgment{'s' if len(project['nodes']) > 1 else ''})")  # main() printed findings


def cmd_compile(project: dict, args) -> None:
    results = execute(project, dry=True, hold=True)  # what `run` would ask, on_change included
    for n in frozen_changes(project):
        print(f"# {n}: on_change: freeze and the spec changed since the last complete run: `run` will refuse "
              f"without --allow-change")
    first = next(n for n in project["order"] if "union" not in project["nodes"][n])
    name = args.node or first
    its = results[name]["items"]
    if its:
        row_items = [it for it in its if it["id"] == its[0]["id"]]
        model = row_items[0]["spec"]["model"]
        if is_llm(model):
            print(f"# {name}: prompt for row {its[0]['id']}, question {row_items[0]['qid']} (one request per question)")
            print(llm_prompt(row_items[0]["aq"], row_items[0]["state"])[0])
        else:
            payload = {"model": model, "state": row_items[0]["state"], "questions": {it["qid"]: it["aq"] for it in row_items}}
            print(f"# {name}: request for row {its[0]['id']} (one request per row, all questions read once"
                  + (f"; engine {model.split(':', 1)[0]!r} is a plugin and asks in its own way)" if engines.get(model) else ")"))
            print(json.dumps(payload, indent=2, ensure_ascii=False))
    total, expected_total = 0.0, 0.0
    print()
    for n in project["order"]:
        res, spec = results[n], project["nodes"][n]
        if "union" in spec:
            print(f"# {n}: union of {', '.join(spec['union'])} ({len(res['rows'])} rows)")
            continue
        todo = [it for it in res["items"] if it["key"] not in res["hits"]]
        big = oversized(res["items"])
        for w in big[:5]:
            print(f"# {n}: size warning: {w}")
        if len(big) > 5:
            print(f"# {n}: … {len(big) - 5} more rows near the limit")
        groups: dict[str, list[dict]] = {}
        for it in todo:
            groups.setdefault(it["id"], []).append(it)
        cost = estimate_cost(spec["model"], list(groups.values()))
        est = cost / price_per_token(spec["model"])
        if res.get("unknown"):  # rows kept only because their where-clause can't be decided yet
            unk = res["unknown_ids"]
            sure = estimate_cost(spec["model"], [g for i, g in groups.items() if i not in unk])
            maybe = cost - sure
            rate = res.get("pass_rate")
            expected_cost = sure + maybe * rate if rate is not None else None
            expected_total = None if expected_total is None or expected_cost is None else expected_total + expected_cost
        elif expected_total is not None:
            expected_total += cost
        total += cost
        bound = "≤ " if res["unknown"] else ""
        where = f", where keeps {bound}{len(res['rows'])}" if "where" in spec else ""
        print(f"# {n}: {res['input']} rows in{where}; {len(res['items'])} answers planned, {len(res['items']) - len(todo)} cached, "
              f"{bound}{len(todo)} to ask, ~{est:,.0f} input tokens, ~${cost:.5f} ({spec['model']})")
        if res["unknown"]:
            exp = res.get("expected")
            print(f"#   {res['unknown']} rows kept because the answers their where-clause needs aren't cached yet: "
                  + (f"expected ~{exp:.0f} rows, ~${expected_cost:.5f} (from the {res['pass_rate']:.0%} pass rate of "
                     f"rows already answered)" if exp is not None
                     else f"an upper bound; `hunch run --node {upstream(spec)[0]}` first for the real count"))
    if len(project["nodes"]) > 1:
        print(f"# total: ~${total:.5f}" + (f" upper bound, ~${expected_total:.5f} expected" if expected_total is not None
                                              and expected_total < total else ""))


def cmd_run(project: dict, args) -> None:
    from hunch import presentation

    if getattr(args, "node", None):
        project = with_upstream(project, pick(project, args.node))
    changed = frozen_changes(project)
    if changed and not getattr(args, "allow_change", False):
        sys.exit(f"on_change: freeze, and {changed} changed since the last complete run: nothing asked. "
                 f"`hunch diff` shows what the change does; `hunch run --allow-change` accepts it")
    started = time.strftime("%Y-%m-%dT%H:%M:%S")
    run_id = f"{started}-{digest([started, os.getpid(), time.time_ns()])[:6]}"
    def failed(names: list[str], e: BaseException) -> None:  # recorded too; their tables keep the last complete run
        for n in names:
            spec = project["nodes"][n]
            record_run(open_store(spec), {"run_id": run_id, "judgment": table_name(spec), "spec_hash": spec_hash(spec),
                                          "git_sha": git_sha(spec["_dir"]), "model": spec.get("model", ""), "rows": 0,
                                          "asked": 0, "cost": 0.0, "status": f"failed: {e!r}"[:200],
                                          "started_at": started, "finished_at": time.strftime("%Y-%m-%dT%H:%M:%S")})

    try:
        results = execute(project, hold=True)
    except BaseException as e:
        failed(project["order"], e)
        raise
    if settings.SAMPLE is not None:  # a sample must never replace a table that downstream readers take for the whole
        presentation.run(project, args, results, presentation.relative(store_path(project["nodes"][project["order"][0]]["_dir"])), None,
                         sample=settings.SAMPLE)
        return
    for i_node, n in enumerate(project["order"]):
        res, spec = results[n], project["nodes"][n]
        db = open_store(spec)
        nq = max(1, len(question_of(spec))) if "union" not in spec else 1
        keys = None
        try:  # lineage first, the table last: a failure here leaves the previous table in place
            if "union" not in spec:
                keys = {}
                for i, it in enumerate(res["items"]):
                    keys.setdefault(i // nq, {})[f"{it['qid']}_key"] = it["key"]
                record_row_answers(db, table_name(spec), [it for it in res["items"] if it["key"] in res["answers"]], run_id)
            materialize(spec, db, res["rows"], run_id, keys, output_columns(project, results, n))
        except BaseException as e:
            failed(project["order"][i_node:], e)
            raise
        record_run(db, {"run_id": run_id, "judgment": table_name(spec), "spec_hash": spec_hash(spec), "git_sha": git_sha(spec["_dir"]),
                        "model": spec.get("model", ""), "rows": len(res["rows"]), "asked": res["stats"]["asked"],
                        "cost": round(res["stats"]["cost"], 6), "status": "complete", "started_at": started,
                        "finished_at": time.strftime("%Y-%m-%dT%H:%M:%S")})
        if "union" in spec:
            detail(f"{n}: union of {len(spec['union'])} judgments → table \"{n}{spec.get('_table_suffix', '')}\" ({len(res['rows'])} rows)")
            continue
        where = f", where kept {len(res['rows'])}" if "where" in spec else ""
        if settings.VERBOSE:
            print_stats(res["stats"], f"{n}: {res['input']} rows in{where}; answers")
        for qid, q in spec["questions"].items():
            if "act" in q:
                k = sum(1 for r in res["rows"] if r.get(f"{qid}_route") == "review")
                detail(f"  {qid}: {k} rows below act={q['act']} → review queue")
    if len(project["nodes"]) > 1 and settings.VERBOSE:
        print_stats(merge_stats(*(r["stats"] for r in results.values())), "total")
    where = store_path(project["nodes"][project["order"][0]]["_dir"])
    presentation.run(project, args, results, presentation.relative(where), run_id, sample=None)


def cmd_distill(project: dict, args) -> None:
    """A small local model per question, trained on the answers already in the store; asks nothing."""
    from hunch import distill
    node = args.node or next((n for n in reversed(project["order"]) if "union" not in project["nodes"][n]), "")
    if node not in project["nodes"] or "union" in project["nodes"][node]:
        sys.exit(f"--node {node!r}: pick a judgment with questions ({', '.join(project['order'])})")
    out, report = distill.distill(project, node)
    rel = os.path.relpath(out, project["nodes"][node]["_dir"])
    print(f"{node}: distilled to {out}\n" + "\n".join(report))
    print(f"measure it (free, local): hunch test {args.path} --model distilled:{rel}\n"
          f"use it: model: distilled:{rel}, with escalate: {{model: {project['nodes'][node]['model']}}} and act "
          f"on each question, so answers it isn't sure of go to {project['nodes'][node]['model']}")


def cmd_docs(project: dict, args) -> None:
    from hunch.docs import write_docs  # the page generator is its own module; core stays the engine
    page = write_docs(project)
    if getattr(args, "open", False):
        import webbrowser
        webbrowser.open(page.resolve().as_uri())


def cmd_show(project: dict, args) -> None:
    from hunch.inspect import show
    show(project, args)


def main() -> None:
    commands = {"lint": cmd_lint, "compile": cmd_compile, "run": cmd_run, "test": cmd_test, "suggest": cmd_suggest,
                "diff": cmd_diff, "review": cmd_review, "docs": cmd_docs, "distill": cmd_distill,
                "show": cmd_show}
    descriptions = {
        "lint": "Check a spec without asking the model.",
        "compile": "Preview a request and estimate the cost of a run.",
        "run": "Judge rows, cache answers, and write tables.",
        "show": "Inspect the last materialized run without asking the model.",
        "test": "Measure answers against gold and evaluate configured checks.",
        "diff": "Compare today's answers with an older spec or another model.",
        "review": "Build gold by reviewing disagreements and spot checks.",
        "docs": "Generate a shareable HTML report from the last test.",
        "suggest": "Try and measure question rewrites.",
        "distill": "Train a small local model from cached answers.",
    }
    p = argparse.ArgumentParser(prog="hunch", description="Run, inspect, and measure model judgments.",
                                epilog="Other commands: hunch ask, init, plugins, install, skill, hook.")
    p.add_argument("--version", action="version", version=f"hunch {__import__('hunch').__version__}")
    sub = p.add_subparsers(dest="command", required=True, metavar="COMMAND")
    for command, desc in descriptions.items():
        sp = sub.add_parser(command, help=desc, description=desc)
        sp.add_argument("path", type=Path, help="a spec file or folder of specs")
        if command in {"compile", "run", "show", "test", "diff", "review", "suggest", "distill"}:
            sp.add_argument("--node", help="one judgment in a project")
        if command in {"diff", "review"}:
            sp.add_argument("--against", help="old spec/project path or git:REF")
        if command in {"lint", "compile", "run", "test", "diff", "review", "suggest", "distill"}:
            sp.add_argument("--source", type=Path, help="use this CSV for the root judgments")
            sp.add_argument("--traffic", action="store_true", help="use rows logged by judge(..., shadow=...)")
        if command in {"compile", "run", "test", "diff", "review", "suggest", "distill"}:
            sp.add_argument("--max-cost", type=float, help="maximum USD to spend on missing answers")
        if command in {"compile", "run", "test", "diff"}:
            sp.add_argument("--sample", type=int, help="judge a repeatable sample of N root rows")
        if command in {"run", "test", "diff"}:
            sp.add_argument("--verbose", action="store_true", help="show diagnostic tables and request details")
        sp.add_argument("--model", help="use another engine; keeps its tables separate")
        sp.add_argument("--target", help="use settings from targets.NAME in the spec")
        if command == "run":
            sp.add_argument("--allow-change", action="store_true", help="accept a spec change under on_change: freeze")
        if command == "test":
            sp.add_argument("--receipt", action="store_true", help="write a stable results file beside the spec")
        if command == "review":
            sp.add_argument("--list", action="store_true", help="print the queue without prompting")
            sp.add_argument("--limit", type=int, help="review at most N items")
            sp.add_argument("--audit", type=int, default=30, help="random agreeing rows to audit (default 30)")
            sp.add_argument("--reviewer", help="name recorded with each verdict")
        if command == "show":
            sp.add_argument("--id", help="show all stored fields for one row key")
            sp.add_argument("--limit", type=int, default=20, help="rows to list (default 20)")
        if command == "docs":
            sp.add_argument("--open", action="store_true", help="open the generated HTML in a browser")
        if command == "suggest":
            sp.add_argument("--question", help="question to rewrite")
            sp.add_argument("--n", type=int, default=3, help="rewrites to try (default 3)")
            sp.add_argument("--writer", default=WRITER, help=f"LLM that writes rewrites (default {WRITER})")
    if len(sys.argv) == 1:
        p.print_help()
        return
    args = p.parse_args()
    for name, default in {"node": None, "against": None, "source": None, "traffic": False, "max_cost": None,
                          "sample": None, "verbose": False, "receipt": False}.items():
        if not hasattr(args, name):
            setattr(args, name, default)
    settings.SAMPLE, settings.TARGET, settings.STORE = args.sample, None, None
    settings.VERBOSE = args.verbose
    settings.MAX_COST = args.max_cost if args.max_cost is not None else settings.MAX_COST  # else $HUNCH_MAX_COST, if set
    project = load_project(args.path)
    project["target"] = args.target
    if args.model:  # another engine on the same specs: its tables get a suffix, the spec's own stay untouched
        for spec in project["nodes"].values():
            spec["_table_suffix"] = "__" + re.sub(r"\W+", "_", args.model).strip("_")
            if "union" not in spec:
                spec["model"] = args.model
    if args.source and args.traffic:
        sys.exit("--source or --traffic, not both")
    for n in roots(project):
        if args.source:
            project["nodes"][n]["source"] = args.source.resolve()
        elif args.traffic:
            project["nodes"][n]["source"] = traffic_source(project["nodes"][n])
    errors, warnings = (lint_targets(project), []) if args.command == "show" else lint(project)
    if args.traffic:  # live traffic has no gold columns: expected, so one line instead of one per question
        nogold = [w for w in warnings if "gold column" in w]
        warnings = [w for w in warnings if w not in nogold]
        if nogold:
            print(f"note: logged traffic has no gold ({len(nogold)} questions); `hunch review --traffic` builds it",
                  file=sys.stderr)
    for w in warnings:
        print(f"lint warning: {w}", file=sys.stderr)
    for e in errors:
        print(f"lint error: {e}", file=sys.stderr)
    if errors:
        sys.exit(2)
    if args.target:
        if args.receipt:
            sys.exit("--receipt records the spec as written; run it without --target")
        run = apply_target(project, args.target, keep_model=bool(args.model))
        settings.TARGET, settings.STORE = args.target, run.get("store")  # None: the usual store
        settings.SAMPLE = args.sample if args.sample is not None else run.get("sample")
        if args.max_cost is None and "max_cost" in run:  # a cap: $HUNCH_MAX_COST still holds if it is lower
            settings.MAX_COST = run["max_cost"] if settings.MAX_COST is None else min(settings.MAX_COST, run["max_cost"])
    commands[args.command](project, args)


if __name__ == "__main__":
    main()
