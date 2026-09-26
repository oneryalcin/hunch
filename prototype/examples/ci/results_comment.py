"""A pull-request comment from hunch's results.json, reading only the published format (results.schema.json).

    python results_comment.py .hunch/target/evals.json > comment.md

An example of building on the files hunch writes without importing hunch: standard library only, version-checked.
"""
import json
import sys

doc = json.load(open(sys.argv[1]))
if doc.get("version") != 1:
    sys.exit(f"results.json version {doc.get('version')}: this script reads version 1")


def pct(x):
    return "–" if x is None else f"{x:.1%}"


lines = [f"### hunch: {'passed' if doc['passed'] else '**failed**'}" + (f" (sample of {doc['sample']})" if doc.get("sample") else "")]
lines += ["", "| judgment | question | accuracy | range | acted on | wrong when acted | failed checks |", "|---|---|---|---|---|---|---|"]
for jname, j in doc["judgments"].items():
    for qname, q in j["questions"].items():
        acc, act = q.get("accuracy") or {}, q.get("act") or {}
        ci = acc.get("ci")
        failed = [c["check"] + (" (warn)" if c.get("severity") == "warn" else "") for c in q.get("checks", []) if not c["passed"]]
        lines.append(f"| {jname} | {qname} | {pct(acc.get('value'))} | {pct(ci[0]) + '–' + pct(ci[1]) if ci else '–'} | "
                     f"{pct(act.get('automated'))} | {pct(act.get('wrong'))} | {', '.join(failed) or '–'} |")
    bad = [e["name"] for e in j.get("examples", []) if not e["passed"]]
    if j.get("examples"):
        lines.append(f"\n{jname}: {len(j['examples']) - len(bad)}/{len(j['examples'])} examples pass" + (f"; failing: {', '.join(bad)}" if bad else ""))
print("\n".join(lines))
