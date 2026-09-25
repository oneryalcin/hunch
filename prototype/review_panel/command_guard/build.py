"""Blind packets for the command guard: a random 100 commands (fixed by hash; `audit`) plus every command the guard
flags at p(yes) >= 0.5 on any question (`uncertain`: picked for a reason, gold but not in the estimate).
Reviewers see what the model sees (redacted, clipped) and the spec's own question texts; no model answers."""
import hashlib
import json
import sys
from pathlib import Path

R = Path(__file__).parent
sys.path.insert(0, str(R.parents[2] / "src"))
import hunch  # noqa: E402
import hunch.core as core  # noqa: E402  (engine internals: rows, state)

SPEC = R.parent.parent / "examples/claude_code/command_guard.yml"
spec = core.load_spec(SPEC)
Q = list(spec["questions"])
res = {r["id"]: r for r in hunch.results(SPEC)}
rows = [{**r, **core.state_of(spec, r)} for r in core.rows(spec)]
by_hash = sorted(rows, key=lambda r: hashlib.sha256(r["id"].encode()).hexdigest())
audit = by_hash[:100]
flagged = [r for r in by_hash[100:] if any(float(res[r["id"]][q + "_pyes"]) >= 0.5 for q in Q)]
sample = audit + flagged
order = sorted(sample, key=lambda r: hashlib.sha256(("mix" + r["id"]).encode()).hexdigest())  # hide which is which
items = [{"n": i, **{c: r[c] for c in spec["state"]}} for i, r in enumerate(order, 1)]
json.dump({str(i["n"]): {"id": r["id"], "kind": "audit" if r in audit else "uncertain"} for i, r in zip(items, order)},
          open(R / "key/map.json", "w"), indent=1)
questions = {q: {"question": spec["questions"][q]["instructions"], **spec["questions"][q]["criteria"]} for q in Q}
for name, rs in [("opus", items), ("sonnet_a", items), ("sonnet_b", items[::-1])]:
    json.dump({"questions": questions, "rows": rs}, open(R / f"packets/{name}.json", "w"), indent=1, ensure_ascii=False)
print(f"{len(audit)} audit + {len(flagged)} flagged = {len(items)} rows; {len(json.dumps(items)) // 1000} KB per packet")
