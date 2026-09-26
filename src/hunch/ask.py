"""hunch ask: a question and a CSV in, every row answered, no YAML to write first.

    hunch ask "Would this command destroy files or data?" commands.csv
    hunch ask "Which team should handle this?" messages.csv --options billing,technical,sales

It writes the spec a person would have written (`<name>.yml`, next to where you run it), runs it like `hunch run`,
prints how the answers split and the rows it was least sure of, and writes every answer to `<name>.answers.csv`.
The spec is the point: from there `hunch review` builds an answer key, `hunch test` measures it, and an edited
question goes through `hunch diff`. Asking the same question again reads the store and costs nothing.
"""
import argparse
import csv
import os
import re
import sys
from collections import Counter
from pathlib import Path

from hunch import core

MODEL = "jev-1.13.0"
ACT = 0.9  # a first bar for acting alone; below it a row is "unsure" (tune it later from `hunch test`'s dial)
SHOW = 10  # unsure rows printed


FILLER = set("a an the is are was were be do does did would will should could can this that these those it its "
              "of to in on for by with as at or and any there here what which who whom whose how".split())


def name_of(question: str) -> str:
    """"Would running this command delete files?" → running_command_delete_files."""
    words = [w for w in re.findall(r"\w+", question.lower()) if w not in FILLER]  # any script, not only Latin
    return "_".join(words[:4]) or "question"


def build(question: str, source: Path, options: list[str], columns: list[str] | None, name: str) -> dict:
    """The spec for this question over this CSV: every column but the key and gold_* is the state, unless
    `columns` says which."""
    try:  # read it all once: a bad byte deep in the file should stop here, not halfway through a run
        with open(source, newline="", encoding="utf-8") as f:
            reader = csv.reader(f)
            header = next(reader, [])
            rows = sum(1 for _ in reader)
    except UnicodeDecodeError as e:
        sys.exit(f"{source}: not UTF-8 ({e.reason} at byte {e.start}); convert it first, e.g. "
                 f"iconv -f latin1 -t utf-8 {source} > {source.stem}.utf8.csv")
    if not header:
        sys.exit(f"{source}: no header row")
    if not rows:
        sys.exit(f"{source}: no rows under the header")
    key = "id" if "id" in header else header[0]
    missing = [c for c in columns or [] if c not in header]
    if missing:
        sys.exit(f"--columns {missing}: not in {source} ({', '.join(header)})")
    state = columns or [c for c in header if c != key and not c.startswith("gold_")] or [key]
    q = {"type": "choice", "instructions": question, "criteria": {o: "" for o in options}} if options \
        else {"type": "noul", "instructions": question}
    return {"judgment": name, "model": MODEL, "source": str(source), "key": key, "state": state,
            "questions": {name: q | {"act": ACT}}}


def main(argv: list[str]) -> None:
    p = argparse.ArgumentParser(prog="hunch ask", description="Answer a question for every row of a CSV.")
    p.add_argument("question", help="the question, as you would ask a person (yes/no unless --options)")
    p.add_argument("source", type=Path, help="a CSV with a header row")
    p.add_argument("--options", help="comma-separated answers to choose one of (default: yes or no)")
    p.add_argument("--columns", help="comma-separated columns the model may see (default: all but the key and gold_*)")
    p.add_argument("--name", help="the spec's name (default: from the question's first words)")
    p.add_argument("--max-cost", type=float, help="USD: refuse or stop above this (default: $HUNCH_MAX_COST)")
    a = p.parse_args(argv)
    if not a.question.strip():
        sys.exit("the question is empty")
    if a.name and not re.fullmatch(r"[\w-]+", a.name.removesuffix(".yml")):
        sys.exit(f"--name {a.name!r}: letters, digits, _ and - only (it names the spec file)")
    if not a.source.is_file():
        sys.exit(f"{a.source}: no such file")
    if a.max_cost is not None:
        core.MAX_COST = a.max_cost
    name = a.name.removesuffix(".yml") if a.name else name_of(a.question)
    options = list(dict.fromkeys(o.strip() for o in a.options.split(",") if o.strip())) if a.options else []
    if a.options and len(options) < 2:
        sys.exit("--options needs at least two different answers, comma-separated")
    path = Path(f"{name}.yml")
    spec = build(a.question, Path(os.path.relpath(a.source.resolve(), Path.cwd())), options,
                 [c.strip() for c in a.columns.split(",")] if a.columns else None, name)
    text = core.spec_yaml(spec)
    if path.exists() and path.read_text() != text:
        sys.exit(f"{path} exists and differs (another question or columns, or your edits); --name another, "
                 f"or `hunch run {path}`")
    new = not path.exists()
    path.write_text(text)
    project = core.load_project(path.resolve())
    errors, warnings = core.lint(project)
    for w in warnings:
        print(f"lint warning: {w}", file=sys.stderr)
    if errors:
        if new:  # nothing left behind for a spec that can't run
            path.unlink()
        sys.exit("\n".join(f"lint error: {e}" for e in errors))
    core.cmd_run(project, argparse.Namespace())
    res = core.execute(project, dry=True)[name]  # everything is in the store now: nothing is asked
    rows = []
    for it in res["items"]:
        label, _, _ = core.decide(res["answers"][it["key"]])
        conf = core.conf_of(it, res["answers"][it["key"]])
        # named as `hunch run`'s table names them: <question>, <question>_p, <question>_route
        rows.append({**{c: it["row"].get(c, "") for c in [spec["key"], *spec["state"]]},
                     name: label, f"{name}_p": round(conf, 3), f"{name}_route": core.route(it["q"], res["answers"][it["key"]])})
    counts = Counter(r[name] for r in rows)
    print(f"\n{a.question}")
    for label, n in counts.most_common():
        print(f"  {label:<20} {n:>6}  ({n / len(rows):.0%})")
    unsure = sorted((r for r in rows if r[f"{name}_route"] == "review"), key=lambda r: r[f"{name}_p"])
    print(f"  sure enough to act on (p ≥ {ACT}): {len(rows) - len(unsure)} of {len(rows)}")
    if unsure:
        print(f"\nleast sure ({min(SHOW, len(unsure))} of {len(unsure)}):")
        for r in unsure[:SHOW]:
            shown = " | ".join(str(r[c]) for c in spec["state"]).replace("\n", " ")
            print(f"  {r[spec['key']]!s:>8}  {r[name]:<12} p={r[f'{name}_p']:.2f}  {shown[:80]}")
    out = Path(f"{name}.answers.csv")
    with open(out, "w", newline="") as f:
        w = csv.DictWriter(f, list(rows[0]))
        w.writeheader()
        w.writerows(rows)
    print(f"\nanswers: {out}   spec: {path}\n"
          f"next: `hunch review {path}` (a verdict on the rows that teach the most), then `hunch test {path}` "
          f"(how often it's right, and where to set act); commit it, and after an edit `hunch diff {path} --against git:HEAD` "
          f"shows every answer the edit flips")
