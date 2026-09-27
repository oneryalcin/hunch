"""Human-sized summaries of run and test results. All figures come from the engine's result data."""

import shlex
import shutil
import textwrap
from collections import Counter
from pathlib import Path


def width() -> int:
    return max(40, shutil.get_terminal_size(fallback=(80, 24)).columns)


def line(text: str, indent: str = "  ") -> None:
    print(textwrap.fill(str(text), width=width(), initial_indent=indent, subsequent_indent=indent,
                        break_long_words=False, break_on_hyphens=False))


def money(value: float) -> str:
    return f"${value:.5f}"


def next_command(command: str, path: Path, args, *, cost: bool = False, same_rows: bool = False) -> str:
    parts = ["hunch", command, shlex.quote(str(path))]
    if command != "docs" and getattr(args, "node", None):
        parts += ["--node", shlex.quote(args.node)]
    if getattr(args, "model", None):
        parts += ["--model", shlex.quote(args.model)]
    if getattr(args, "target", None):
        parts += ["--target", shlex.quote(args.target)]
    if same_rows:
        if getattr(args, "source", None):
            parts += ["--source", shlex.quote(str(args.source))]
        if getattr(args, "traffic", False):
            parts.append("--traffic")
        if getattr(args, "sample", None):
            parts += ["--sample", str(args.sample)]
    if cost:
        parts += ["--max-cost", "0"]
    return " ".join(parts)


def run(project: dict, args, results: dict, store: Path, run_id: str | None, *, sample: int | None) -> None:
    print(f"Run complete · {args.path}" + (f" (sample of {sample})" if sample else ""))
    for name in project["order"]:
        result = results[name]
        stats = result["stats"]
        row_count = len(result["rows"])
        line(f"{name}: {result['input']} rows in → {row_count} {'judged' if sample else 'written'}")
        line(f"answers: {stats['cached']} cached, {stats['asked']} asked · {money(stats['cost'])}", "    ")
        spec = project["nodes"][name]
        for qid in ([spec["question"]] if "union" in spec else spec["questions"]):
            split = Counter(str(row.get(qid)) for row in result["rows"] if row.get(qid) not in (None, ""))
            if split and len(split) <= 5:
                line(f"{qid}: " + ", ".join(f"{label} {count}" for label, count in split.most_common()))
        if "union" not in spec:
            for qid, q in spec["questions"].items():
                if "act" in q:
                    review = sum(row.get(f"{qid}_route") == "review" for row in result["rows"])
                    if review:
                        line(f"{qid}: {review} routed to review")
    if sample:
        line("Answers were cached; materialized tables were not replaced.")
    else:
        from hunch.store import table_name
        line(f"Tables: {', '.join(table_name(project['nodes'][n]) for n in project['order'])} · Store: {store}")
        line(f"Run ID: {run_id}")
        inspect = next_command('show', args.path, args)
        if len(project["nodes"]) > 1 and not getattr(args, "node", None):
            inspect += " --node NAME"
        line(f"Inspect: {inspect}")
    line(f"Measure: {next_command('test', args.path, args, cost=True, same_rows=True)}")


def _accuracy(q: dict) -> str:
    a = q.get("accuracy")
    if not a:
        return "no reviewed answers to score"
    value = a["value"]
    if a["basis"] == "estimate":
        lo, hi = a["ci"]
        reviewed = sum(x["reviewed"] for x in a.get("reviewed", {}).values())
        return f"estimated accuracy {value:.1%} (95% CI {lo:.1%}–{hi:.1%}; {reviewed} reviewed)"
    gold = q.get("gold", {}).get("rows", 0)
    if q.get("weighted"):
        return f"weighted accuracy {value:.1%} on {gold} gold rows"
    right = round(value * gold)
    return f"{right}/{gold} agree with gold ({value:.1%})"


def _checks(part: dict) -> None:
    for check in part.get("checks", []):
        state = "PASS" if check["passed"] else "WARN" if check.get("severity") == "warn" else "FAIL"
        value, limit = check.get("value"), check.get("limit")
        observed = f" · observed {value:g}, limit {limit:g}" if value is not None and limit is not None else ""
        line(f"{state} {check['check']}{observed}", "    ")
    for name in part.get("unavailable_checks", []):
        line(f"NOT ASSESSED {name}: no suitable gold or rows", "    ")


def test(project: dict, args, report: dict, stats: dict, result_path: Path) -> None:
    states = [r["assessment"] for r in report.values()]
    order = ("failed", "warning", "unassessed", "passed", "measured", "no_gold")
    state = next((s for s in order if s in states), "no_gold")
    labels = {"failed": "configured check failed", "warning": "warning check failed", "unassessed": "some checks could not run",
              "passed": "configured checks passed", "measured": "measured; no acceptance checks configured",
              "no_gold": "no gold to measure against"}
    print(f"Test complete · {args.path}")
    line(labels[state].capitalize())
    for name, result in report.items():
        if len(report) > 1:
            print(f"\n{name}")
        for qid, q in result["questions"].items():
            line(f"{qid}: {_accuracy(q)}", "    ")
            if q.get("auroc") is not None:
                line(f"AUROC {q['auroc']:.3f} · calibration error {q['calibration_error']:.3f}", "      ")
            if q.get("baseline") and not q["baseline"].get("model_ahead"):
                line("The baseline rule is as accurate as the model or better on these gold rows.", "      ")
            if q.get("gold", {}).get("excluded"):
                line(f"{q['gold']['excluded']} reviewed row(s) excluded as ambiguous or needing more context.", "      ")
            if q.get("needs_context"):
                line(f"{q['needs_context']} random spot check(s) needed more context than the state supplied.", "      ")
            if q.get("act"):
                act = q["act"]
                wrong = ("no acted-on gold rows to check" if act["wrong"] is None else
                         f"{act['wrong']:.1%} wrong among {act['judged']} acted-on gold rows")
                line(f"Acts alone on {act['automated']:.1%} of rows; {wrong}.", "      ")
            mistakes = q.get("mistakes") or {}
            if mistakes.get("total"):
                ids = ", ".join(str(x["id"]) for x in mistakes.get("most_confident", [])[:3])
                noun = "disagreement" if mistakes["total"] == 1 else "disagreements"
                line(f"{mistakes['total']} {noun} with gold; first: {ids}.", "      ")
            _checks(q)
        for parent, multi in result.get("multi", {}).items():
            if multi.get("exact_set_accuracy") is not None:
                line(f"{parent}: exact-set accuracy {multi['exact_set_accuracy']:.1%} on {multi['gold_rows']} rows", "    ")
            _checks(multi)
        for name_, metric in result.get("metrics", {}).items():
            if metric.get("rate") is not None:
                if metric.get("ci") is not None:
                    lo, hi = metric["ci"]
                    line(f"{name_}: estimated population rate {metric['rate']:.1%} "
                         f"(95% CI {lo:.1%}–{hi:.1%}; {metric['fired']}/{metric['rows']} sampled rows)", "    ")
                else:
                    line(f"{name_}: {metric['fired']}/{metric['rows']} rows ({metric['rate']:.1%})", "    ")
            _checks(metric)
        if result.get("examples"):
            n = sum(x["passed"] for x in result["examples"])
            line(f"Pinned examples: {n}/{len(result['examples'])} matched", "    ")
            for example in result["examples"]:
                if not example["passed"]:
                    line(f"{'WARN' if example['severity'] == 'warn' else 'FAIL'} {example['name']}", "      ")
    line(f"Answers: {stats['cached']} cached, {stats['asked']} asked · {money(stats['cost'])}")
    line(f"Results: {result_path}")
    line(f"More: {next_command('test', args.path, args, cost=True, same_rows=True)} --verbose")
    line(f"Browser report: {next_command('docs', args.path, args)} --open")


def relative(path: Path) -> Path:
    cwd = Path.cwd()
    return path.relative_to(cwd) if path.is_relative_to(cwd) else path
