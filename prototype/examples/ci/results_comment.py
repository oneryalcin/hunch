"""A pull-request comment from hunch's results.json, reading only the published format (results.schema.json).

    python results_comment.py .hunch/target/evals.json > comment.md

An example of building on the files hunch writes without importing hunch: standard library only, version-checked.
"""
import json
import sys
from pathlib import Path


def pct(x):
    return "–" if x is None else f"{x:.1%}"


def failed(checks, unavailable=()):
    bad = [c["check"] + (" (warn)" if c.get("severity") == "warn" else "") for c in checks if not c["passed"]]
    return "; ".join((["FAIL: " + ", ".join(bad)] if bad else [])
                     + (["NOT ASSESSED: " + ", ".join(unavailable)] if unavailable else [])) or "–"


path = Path(sys.argv[1])
if not path.exists():  # `test` removes it when it starts, so a run stopped before the end leaves none
    print(f"### hunch: no results\n\n`{path}` wasn't written: `hunch test` stopped before it finished (see the log).")
    sys.exit(0)
doc = json.loads(path.read_text())
if doc.get("version") not in (1, 2):
    sys.exit(f"results.json version {doc.get('version')}: this script reads versions 1 and 2")

status = doc.get("assessment", "passed" if doc["passed"] else "failed").replace("_", " ")
lines = [f"### hunch: {status}" + (f" (sample of {doc['sample']})" if doc.get("sample") else ""),
         "", "| judgment | question | accuracy | range | acted on | wrong when acted | failed checks |", "|---|---|---|---|---|---|---|"]
for jname, j in doc["judgments"].items():
    for qname, q in j["questions"].items():
        acc, act = q.get("accuracy") or {}, q.get("act") or {}
        ci = acc.get("ci")
        lines.append(f"| {jname} | {qname} | {pct(acc.get('value'))} | {pct(ci[0]) + '–' + pct(ci[1]) if ci else '–'} | "
                     f"{pct(act.get('automated'))} | {pct(act.get('wrong'))} | "
                     f"{failed(q.get('checks', []), q.get('unavailable_checks', []))} |")
    for mname, m in (j.get("multi") or {}).items():
        lines.append(f"| {jname} | {mname} (several apply) | {pct(m.get('exact_set_accuracy'))} exact set | – | – | – | "
                     f"{failed(m.get('checks', []), m.get('unavailable_checks', []))} |")
    for mname, m in (j.get("metrics") or {}).items():
        lines.append(f"| {jname} | metric {mname} | fires on {pct(m.get('rate'))} | – | – | – | "
                     f"{failed(m.get('checks', []), m.get('unavailable_checks', []))} |")
    if j.get("examples"):
        bad = [e["name"] for e in j["examples"] if not e["passed"]]
        lines.append(f"\n{jname}: {len(j['examples']) - len(bad)}/{len(j['examples'])} examples pass" + (f"; failing: {', '.join(bad)}" if bad else ""))
print("\n".join(lines))
