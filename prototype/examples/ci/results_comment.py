"""A pull-request comment from hunch's results.json, reading only the published format (results.schema.json).

    python results_comment.py .hunch/target/evals.json > comment.md

An example of building on the files hunch writes without importing hunch: standard library only, version-checked.
"""
import json
import sys
from pathlib import Path


def pct(x):
    return "–" if x is None else f"{x:.1%}"


def failed(checks):
    return ", ".join(c["check"] + (" (warn)" if c.get("severity") == "warn" else "") for c in checks if not c["passed"]) or "–"


path = Path(sys.argv[1])
if not path.exists():  # `test` removes it when it starts, so a run stopped before the end leaves none
    print(f"### hunch: no results\n\n`{path}` wasn't written: `hunch test` stopped before it finished (see the log).")
    sys.exit(0)
doc = json.loads(path.read_text())
if doc.get("version") != 1:
    sys.exit(f"results.json version {doc.get('version')}: this script reads version 1")

lines = [f"### hunch: {'passed' if doc['passed'] else '**failed**'}" + (f" (sample of {doc['sample']})" if doc.get("sample") else ""),
         "", "| judgment | question | accuracy | range | acted on | wrong when acted | failed checks |", "|---|---|---|---|---|---|---|"]
for jname, j in doc["judgments"].items():
    for qname, q in j["questions"].items():
        acc, act = q.get("accuracy") or {}, q.get("act") or {}
        ci = acc.get("ci")
        lines.append(f"| {jname} | {qname} | {pct(acc.get('value'))} | {pct(ci[0]) + '–' + pct(ci[1]) if ci else '–'} | "
                     f"{pct(act.get('automated'))} | {pct(act.get('wrong'))} | {failed(q.get('checks', []))} |")
    for mname, m in (j.get("multi") or {}).items():
        lines.append(f"| {jname} | {mname} (several apply) | {pct(m.get('exact_set_accuracy'))} exact set | – | – | – | {failed(m.get('checks', []))} |")
    for mname, m in (j.get("metrics") or {}).items():
        lines.append(f"| {jname} | metric {mname} | fires on {pct(m.get('rate'))} | – | – | – | {failed(m.get('checks', []))} |")
    if j.get("examples"):
        bad = [e["name"] for e in j["examples"] if not e["passed"]]
        lines.append(f"\n{jname}: {len(j['examples']) - len(bad)}/{len(j['examples'])} examples pass" + (f"; failing: {', '.join(bad)}" if bad else ""))
print("\n".join(lines))
