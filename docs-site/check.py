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
    "reference/cli.mdx": sorted(set(re.findall(r'"(\w+)": cmd_\w+', code)) | {"init"}
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
}
drift = {name: (a - b, b - a) for name, (a, b) in pairs.items() if a != b}
for name, (extra, absent) in drift.items():
    print(f"spec.schema.json {name}: " + ", ".join([f"not in the code {sorted(extra)}"] * bool(extra) + [f"missing {sorted(absent)}"] * bool(absent)))
print(f"schema: {len(pairs)} key sets " + ("match the code" if not drift else "differ from the code"))
sys.exit(1 if any(missing.values()) or drift else 0)
