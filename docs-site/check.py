"""Fails when the code has something the reference pages don't: a spec key, question key, test, on_change value,
command, CLI flag or environment variable. The reference is the contract; this keeps it complete.

    uv run docs-site/check.py
"""
import re
import sys
from pathlib import Path

HERE = Path(__file__).parent
SRC = HERE.parent / "src" / "hunch"
sys.path.insert(0, str(SRC.parent))
from hunch import core  # noqa: E402

code = (SRC / "core.py").read_text()
env_code = "\n".join(p.read_text() for p in [*SRC.glob("*.py"), *(HERE.parent / "server" / "hunch_server").glob("*.py")])
checks = {
    "reference/spec.mdx": sorted(core.SPEC_KEYS | core.QUESTION_KEYS - {"_multi"} | core.TEST_KEYS | core.METRIC_TEST_KEYS | set(core.ON_CHANGE)),
    "reference/cli.mdx": sorted(set(re.findall(r'"(\w+)": cmd_\w+', code))
                                | set(re.findall(r'sys\.argv\[1\] == "(\w+)"', (SRC / "cli.py").read_text()))
                                | set(re.findall(r'add_argument\("(--[\w-]+)"', code))),
    "reference/environment.mdx": sorted(set(re.findall(r'environ(?:\.get\(|\[)"([A-Z_]+)"', env_code))
                                        | {e["key"] for e in core.ENDPOINTS.values()}),
}
missing = {page: [w for w in words if f"`{w}" not in (HERE / page).read_text()] for page, words in checks.items()}
for page, words in missing.items():
    for w in words:
        print(f"{page}: `{w}` is in the code but not documented")
total = sum(len(v) for v in checks.values())
print(f"checked {total} names across {len(checks)} reference pages: "
      + ("all documented" if not any(missing.values()) else f"{sum(map(len, missing.values()))} missing"))

# The spec schema (for editors) must say what the code says: same keys, same allowed values.
import json  # noqa: E402

from hunch import traces  # noqa: E402

schema = json.loads((SRC / "spec.schema.json").read_text())
d = schema["definitions"]
pairs = {
    "spec keys": (set(schema["properties"]), core.SPEC_KEYS),
    "question keys": (set(d["question"]["properties"]), core.QUESTION_KEYS - {"_multi"}),
    "tests": (set(d["tests"]["properties"]), core.TEST_KEYS | core.METRIC_TEST_KEYS),
    "on_change": (set(schema["properties"].get("on_change", {}).get("enum", [])), set(core.ON_CHANGE)),
    "view": (set(schema["properties"].get("view", {}).get("enum", [])), set(traces.VIEWS)),
    "severity": (set(d.get("severity", {}).get("enum", [])), set(core.SEVERITIES)),
    "exposure keys": (set(schema["properties"]["exposures"]["items"]["properties"]), core.EXPOSURE_KEYS),
    "exposure kinds": (set(schema["properties"]["exposures"]["items"]["properties"]["kind"]["enum"]), set(core.EXPOSURE_KINDS)),
}
drift = {name: (a - b, b - a) for name, (a, b) in pairs.items() if a != b}
for name, (extra, absent) in drift.items():
    print(f"spec.schema.json {name}: " + ", ".join([f"not in the code {sorted(extra)}"] * bool(extra) + [f"missing {sorted(absent)}"] * bool(absent)))
print(f"schema: {len(pairs)} key sets " + ("match the code" if not drift else "differ from the code"))
# A battery's results.json must describe its spec as it is now: an edited battery ships re-measured numbers.
# Receipts for other engines (results__<engine>.json, from --model) are checked against the spec with that engine,
# since the spec hash includes the model. Every judgment must be measured, and nothing measured may be gone.
stale = []
receipts = sorted((SRC / "recipes").glob("*/results*.json"))
for receipt in receipts:
    project = core.load_project(receipt.parent)
    got = json.loads(receipt.read_text())["judgments"]
    where = f"recipes/{receipt.parent.name}/{receipt.name}"
    for name, spec in project["nodes"].items():
        j = got.get(name)
        if j is None:
            stale.append(f"{where}: no numbers for judgment {name!r}")
        elif j["spec_hash"] != core.spec_hash({**spec, **({"model": j["model"]} if "model" in spec and j["model"] else {})}):
            stale.append(f"{where}: {name!r} was measured on another version of the spec")
    stale += [f"{where}: {name!r} is no longer in the battery" for name in got if name not in project["nodes"]]
for s in stale:
    print(f"{s}; re-run `hunch test . --receipt` (with --model <engine> for results__<engine>.json)")
print(f"receipts: {len(receipts)} in {len({r.parent for r in receipts})} batteries, " + ("all current" if not stale else f"{len(stale)} problems"))
sys.exit(1 if any(missing.values()) or drift or stale else 0)
