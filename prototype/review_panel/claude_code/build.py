"""Blind packets for the Claude Code turn judgments: 60 random turns (fixed by hash), no model answers shown."""
# /// script
# requires-python = ">=3.12"
# dependencies = ["httpx", "pyyaml"]
# ///
import hashlib, json, sys
from pathlib import Path

R = Path(__file__).parent
sys.path.insert(0, str(R.parents[2] / "src"))
import hunch.core as hunch  # noqa: E402  (engine internals: items, store, reviews)

spec = hunch.load_spec(R.parent.parent / "examples/claude_code/outcome.yml")
rows = [{**r, **hunch.state_of(spec, r)} for r in hunch.rows(spec)]  # what the judgment sees: redacted, clipped
sample = sorted(rows, key=lambda r: hashlib.sha256(r["id"].encode()).hexdigest())[:60]
items = [{"n": i, "request": r["request"], "final_reply": r["final_reply"], "next_message": r["next_message"]}
         for i, r in enumerate(sample, 1)]
json.dump({str(i["n"]): r["id"] for i, r in zip(items, sample)}, open(R / "key/map.json", "w"), indent=1)
for name, order in [("opus", items), ("sonnet_a", items), ("sonnet_b", items[::-1])]:
    json.dump({"rows": order}, open(R / f"packets/{name}.json", "w"), indent=1, ensure_ascii=False)
print(len(items), "rows;", sum(len(json.dumps(i)) for i in items) // 1000, "KB per packet")
