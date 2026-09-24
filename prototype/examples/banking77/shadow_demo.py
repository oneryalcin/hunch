# /// script
# requires-python = ">=3.12"
# dependencies = ["httpx", "pyyaml"]
# ///
"""Shadow mode: the app answers with the live spec (flat intent.yml) while a candidate (the two-step tree)
answers the same rows on the side. Then compare them on that traffic. $0 after the Phase 2 runs: every
answer is already in the store."""

import csv
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).parent
HUNCH = HERE.parent.parent / "hunch.py"
sys.path.insert(0, str(HUNCH.parent.parent / "src"))
import hunch.core as hunch  # noqa: E402  (engine internals: items, store, reviews)

LIVE, CANDIDATE = HERE / "intent.yml", HERE.parent / "banking77_tree"
hunch.MAX_COST = 0.0

print("1. 'live traffic': 40 customer messages, answered by the live spec; the candidate answers too, unseen")
for r in list(csv.DictReader(open(HERE / "banking77_holdout.csv", newline="")))[:40]:
    live = hunch.judge(LIVE, shadow=CANDIDATE, text=r["text"])  # the app only ever sees `live`
print(f"   last answer the app got: {live}")

print("\n2. shadow report: where would the candidate have answered differently? (all cached)")
cmd = ["uv", "run", "-q", str(HUNCH), "diff", str(CANDIDATE), "--against", str(LIVE), "--node", "intent_tree",
       "--traffic", "--max-cost", "0"]
print("   $ hunch " + " ".join(cmd[4:]).replace(str(HERE.parent) + "/", ""))
print("   " + subprocess.run(cmd, capture_output=True, text=True).stdout.strip().replace("\n", "\n   "))

print("\n3. which one is right? review only the rows where they differ, then run the same diff again:")
print("   $ hunch review banking77_tree --node intent_tree --against banking77/intent.yml --traffic")
