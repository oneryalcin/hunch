# /// script
# requires-python = ">=3.12"
# dependencies = ["httpx", "pyyaml"]
# ///
"""Write the panel's majority verdicts as hunch review verdicts for the BANKING77 holdout.

Replaces examples/banking77/intent.reviews.csv (the earlier verdicts were Claude's own, on disputes only;
see README). Mapping, per row, from the majority of three reviewers:
  disagreement: Jev ok, key not → model_right; key ok, Jev not → key_right; both → both_ok; neither → labeled (majority best)
  agreement (audit): label ok → confirmed; not ok → labeled (majority best) or ambiguous if reviewers split
"""

import csv
import json
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE.parent))
import hunch  # noqa: E402

EX = HERE.parent / "examples" / "banking77"
spec = hunch.load_spec(EX / "intent.yml")
spec["source"] = (EX / "banking77_holdout.csv").resolve()
rows = {r["id"]: r for r in hunch.rows(spec)}

key = json.load(open(HERE / "key/b77_map.json"))
panel: dict[str, dict] = {}
for rev in ["opus", "sonnet_a", "sonnet_b"]:
    for a in json.load(open(HERE / f"answers/b77_{rev}.json")):
        info = key[f"{rev}:{a['n']}"]
        p = panel.setdefault(info["id"], {**info, "g": [], "j": [], "best": []})
        p["g"].append(bool(a["candidates"].get(info["gold"])))
        p["j"].append(bool(a["candidates"].get(info["jev"])))
        p["best"].append(a["best"])

now = datetime.now(timezone.utc).isoformat(timespec="seconds")
out = []
for rid, p in panel.items():
    g, j = sum(p["g"]) >= 2, sum(p["j"]) >= 2
    best, votes = Counter(p["best"]).most_common(1)[0]
    if p["kind"] == "disagree":
        verdict, label = {(False, True): ("model_right", p["jev"]), (True, False): ("key_right", p["gold"]),
                          (True, True): ("both_ok", f"{p['gold']}|{p['jev']}"), (False, False): ("labeled", best)}[(g, j)]
    elif g:
        verdict, label = "confirmed", p["gold"]
    else:
        verdict, label = ("labeled", best) if votes >= 2 and best != p["gold"] else ("ambiguous", "")
    it = hunch.item(spec, rows[rid], "intent")
    out.append({"qid": "intent", "row_id": rid, "state_hash": it["shash"], "verdict": verdict, "label": label,
                "reviewer": "panel:opus+sonnet+sonnet", "at": now,
                "kind": "disputed" if p["kind"] == "disagree" else "audit"})  # agreements were a random sample

path = EX / "intent.reviews.csv"
with open(path, "w", newline="") as f:
    w = csv.DictWriter(f, fieldnames=hunch.REVIEW_FIELDS, lineterminator="\n")
    w.writeheader()
    w.writerows(sorted(out, key=lambda r: int(r["row_id"])))
print(f"wrote {len(out)} verdicts to {path.name}:", dict(Counter(r["verdict"] for r in out)))
