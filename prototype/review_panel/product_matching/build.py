"""Blind packets for same_product: a random 100 pairs (fixed by hash; `audit`) plus every other pair where the model
and the benchmark's answer key disagree (`disputed`: picked for a reason, gold but not in the estimate).
Reviewers see what the model sees and the spec's own question; no model answers and no answer key.

    python build.py            # round 1: the random 100 and every disagreement
    python build.py round2     # a later round: disagreements of the spec as it is now that have no verdict yet"""
import csv
import hashlib
import json
import sys
from pathlib import Path

R = Path(__file__).parent
sys.path.insert(0, str(R.parents[2] / "src"))
import hunch  # noqa: E402
from hunch.answers import reviews_path  # noqa: E402  (engine internals: rows, state)
from hunch.spec import load_spec, state_of  # noqa: E402  (engine internals: rows, state)
from hunch.spec import rows as source_rows  # noqa: E402

SPEC = R.parent.parent / "examples/product_matching/same_product.yml"
spec = load_spec(SPEC)
res = {r["id"]: r for r in hunch.results(SPEC)}
rows = [{**r, **state_of(spec, r)} for r in source_rows(spec)]
by_hash = sorted(rows, key=lambda r: hashlib.sha256(r["id"].encode()).hexdigest())
ROUND = sys.argv[1] if len(sys.argv) > 1 else ""
done = {v["row_id"] for v in csv.DictReader(open(reviews_path(spec)))} if ROUND else set()
audit = [] if ROUND else by_hash[:100]
disputed = [r for r in by_hash if r not in audit and r["id"] not in done and res[r["id"]]["same"] != r["gold_same"]]
sample = audit + disputed
order = sorted(sample, key=lambda r: hashlib.sha256(("mix" + r["id"]).encode()).hexdigest())  # hide which is which
items = [{"n": i, **{c: r[c] for c in spec["state"]}} for i, r in enumerate(order, 1)]
json.dump({str(i["n"]): {"id": r["id"], "kind": "audit" if r in audit else "disputed"} for i, r in zip(items, order)},
          open(R / f"key/{ROUND or 'map'}.json", "w"), indent=1)
question = {"same": {"question": spec["questions"]["same"]["instructions"],
                     "yes": "They are the same product.", "no": "They are different products.",
                     "unclear": "The two listings don't show enough to tell."}}
for name, rs in [("opus", items), ("sonnet_a", items), ("sonnet_b", items[::-1])]:
    json.dump({"questions": question, "rows": rs}, open(R / f"packets/{ROUND + '_' if ROUND else ''}{name}.json", "w"), indent=1, ensure_ascii=False)
print(f"{len(audit)} audit + {len(disputed)} disputed = {len(items)} rows; {len(json.dumps(items)) // 1000} KB per packet")
