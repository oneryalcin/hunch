"""Write the panel's majority verdicts as hunch reviews for examples/claude_code/command_guard.yml, and print how
often the reviewers agreed. Two of three on yes or no is a label; a majority of "unclear" is needs_context (the
row does not show enough); anything else is ambiguous. The random 100 are `audit` (they feed the estimate); the
flagged rest are `uncertain` (gold, but picked for a reason). `hunch test` then scores the guard."""
import csv
import json
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE.parents[2] / "src"))
import hunch.core as core  # noqa: E402  (engine internals: items, reviews)

REVIEWERS = ["opus", "sonnet_a", "sonnet_b"]
spec = core.load_spec(HERE.parent.parent / "examples/claude_code/command_guard.yml")
rows = {r["id"]: r for r in core.rows(spec)}
key = json.load(open(HERE / "key/map.json"))
answers = {r: {str(a["n"]): a for a in json.load(open(HERE / f"answers/{r}.json"))} for r in REVIEWERS}
missing = {r: len(set(key) - set(a)) for r, a in answers.items() if set(key) - set(a)}
if missing:
    sys.exit(f"answers missing rows: {missing}")

now = datetime.now(timezone.utc).isoformat(timespec="seconds")
out, unanimous = [], Counter()
for n, k in key.items():
    for q in spec["questions"]:
        votes = [answers[r][n][q] for r in REVIEWERS]
        label, count = Counter(votes).most_common(1)[0]
        unanimous[q] += count == 3
        verdict = ("needs_context" if label == "unclear" else "labeled") if count >= 2 else "ambiguous"
        out.append({"qid": q, "row_id": k["id"], "state_hash": core.item(spec, rows[k["id"]], q)["shash"],
                    "verdict": verdict, "label": label if verdict == "labeled" else "",
                    "reviewer": "panel:opus+sonnet+sonnet", "at": now, "kind": k["kind"]})

with open(core.reviews_path(spec), "w", newline="") as f:
    w = csv.DictWriter(f, fieldnames=core.REVIEW_FIELDS, lineterminator="\n")
    w.writeheader()
    w.writerows(sorted(out, key=lambda r: (r["qid"], r["row_id"])))
print(f"{len(out)} verdicts → {core.reviews_path(spec).name}")
print("verdicts:", dict(Counter(r["verdict"] for r in out)))
print("unanimous:", {q: f"{c}/{len(key)}" for q, c in unanimous.items()})
