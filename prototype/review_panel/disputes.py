# /// script
# requires-python = ">=3.12"
# dependencies = []
# ///
"""Append the panel's verdicts on another judgment's own BANKING77 disputes to the shared reviews file.

    uv run disputes.py tree        # the two-step tree: 24 rows (Phase 2)
    uv run disputes.py deepseek    # the same spec on DeepSeek: 13 rows

The first panel reviewed the *flat* model's disagreements with the answer key. Another judgment (the tree,
another engine) disagrees on rows where flat agreed with the key, so its accuracy can't be estimated honestly
until those are reviewed too. Same blind protocol and reviewers; packets, answers and key are in <dir>/.
Verdicts are about the data (is the key's label acceptable? is the other one?), so they hold for any judgment
asking this question.
"""

import csv
import json
import sys
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

HERE = Path(__file__).parent / sys.argv[1]
REVIEWS = Path(__file__).parent.parent / "examples" / "banking77" / "intent.reviews.csv"

key = json.load(open(HERE / "key/b77_map.json"))
panel: dict[str, dict] = {}
for rev in ["opus", "sonnet_a", "sonnet_b"]:
    for a in json.load(open(HERE / f"answers/b77_{rev}.json")):
        info = key[f"{rev}:{a['n']}"]
        p = panel.setdefault(info["id"], {**info, "g": [], "j": [], "best": []})
        p["g"].append(bool(a["candidates"].get(info["gold"])))
        p["j"].append(bool(a["candidates"].get(info.get("model") or info["jev"])))
        p["best"].append(a["best"])

existing = {r["row_id"] for r in csv.DictReader(open(REVIEWS, newline=""))}
now = datetime.now(UTC).isoformat(timespec="seconds")
out = []
for rid, p in panel.items():
    assert rid not in existing, f"row {rid} already reviewed"
    g, j = sum(p["g"]) >= 2, sum(p["j"]) >= 2
    best = Counter(p["best"]).most_common(1)[0][0]
    m = p.get("model") or p["jev"]
    verdict, label = {(False, True): ("model_right", m), (True, False): ("key_right", p["gold"]),
                      (True, True): ("both_ok", f"{p['gold']}|{m}"), (False, False): ("labeled", best)}[(g, j)]
    out.append({"qid": "intent", "row_id": rid, "state_hash": p["state_hash"], "verdict": verdict, "label": label,
                "reviewer": "panel:opus+sonnet+sonnet", "at": now, "kind": "disputed"})  # picked because it disagreed

with open(REVIEWS, "a", newline="") as f:
    w = csv.DictWriter(f, fieldnames=["qid", "row_id", "state_hash", "verdict", "label", "reviewer", "at", "kind"], lineterminator="\n")
    w.writerows(sorted(out, key=lambda r: int(r["row_id"])))
print(f"appended {len(out)} verdicts:", dict(Counter(r["verdict"] for r in out)))
agree = sum(len(set(p["g"])) == 1 and len(set(p["j"])) == 1 for p in panel.values())
print(f"reviewers unanimous on {agree}/{len(panel)}")
