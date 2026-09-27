"""Blind packets for the Claude Code turn judgments: 60 random turns (fixed by hash), no model answers shown."""
# /// script
# requires-python = ">=3.12"
# dependencies = ["httpx", "pyyaml"]
# ///
import hashlib
import json
import sys
from pathlib import Path

R = Path(__file__).parent
sys.path.insert(0, str(R.parents[2] / "src"))
from hunch.spec import load_spec, state_of  # noqa: E402  (engine internals: items, store, reviews)
from hunch.spec import rows as source_rows  # noqa: E402

spec = load_spec(R.parent.parent / "examples/claude_code/outcome.yml")
rows = [{**r, **state_of(spec, r)} for r in source_rows(spec)]  # what the judgment sees: redacted, clipped
sample = sorted(rows, key=lambda r: hashlib.sha256(r["id"].encode()).hexdigest())[:60]
items = [{"n": i, "request": r["request"], "final_reply": r["final_reply"], "next_message": r["next_message"]}
         for i, r in enumerate(sample, 1)]
json.dump({str(i["n"]): r["id"] for i, r in zip(items, sample)}, open(R / "key/map.json", "w"), indent=1)
for name, order in [("opus", items), ("sonnet_a", items), ("sonnet_b", items[::-1])]:
    json.dump({"rows": order}, open(R / f"packets/{name}.json", "w"), indent=1, ensure_ascii=False)
print(len(items), "rows;", sum(len(json.dumps(i)) for i in items) // 1000, "KB per packet")
