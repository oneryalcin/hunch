# /// script
# requires-python = ">=3.12"
# dependencies = ["httpx", "pyyaml"]
# ///
"""Write the panel's majority labels as hunch reviews for examples/claude_code (outcome, claims_done).
The 60 turns are a random sample and there is no source answer key, so every verdict is `labeled`, kind `audit`."""
import csv
import json
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE.parent.parent))
import hunch  # noqa: E402

EX = HERE.parent.parent / "examples" / "claude_code"
ids = json.load(open(HERE / "key/map.json"))
answers = {r: {str(a["n"]): a for a in json.load(open(HERE / f"answers/{r}.json"))} for r in ["opus", "sonnet_a", "sonnet_b"]}
now = datetime.now(timezone.utc).isoformat(timespec="seconds")
for judgment, qid in [("outcome", "outcome"), ("claims", "claims_done")]:
    spec = hunch.load_spec(EX / f"{judgment}.yml")
    rows = {r["id"]: r for r in hunch.rows(spec)}
    out = []
    for n, rid in ids.items():
        label, votes = Counter(answers[r][n][qid] for r in answers).most_common(1)[0]
        verdict = "labeled" if votes >= 2 else "ambiguous"
        out.append({"qid": qid, "row_id": rid, "state_hash": hunch.item(spec, rows[rid], qid)["shash"],
                    "verdict": verdict, "label": label if votes >= 2 else "",
                    "reviewer": "panel:opus+sonnet+sonnet", "at": now, "kind": "audit"})
    with open(hunch.reviews_path(spec), "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=hunch.REVIEW_FIELDS, lineterminator="\n")
        w.writeheader()
        w.writerows(sorted(out, key=lambda r: r["row_id"]))
    print(f"{len(out)} verdicts → {hunch.reviews_path(spec).name}")
