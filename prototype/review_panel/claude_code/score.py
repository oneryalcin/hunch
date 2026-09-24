"""Score the blind panel against Jev's `outcome` and `claims_done` on 60 random Claude Code turns.
Run after `hunch.py run examples/claude_code` (reads Jev's answers from the materialized tables)."""
import json
import math
import sqlite3
from collections import Counter
from pathlib import Path

R = Path(__file__).parent
REVIEWERS = ["opus", "sonnet_a", "sonnet_b"]
ids = json.load(open(R / "key/map.json"))
votes = {n: {} for n in ids}  # n → reviewer → answer
for rev in REVIEWERS:
    for a in json.load(open(R / f"answers/{rev}.json")):
        votes[str(a["n"])][rev] = a
assert all(len(v) == 3 for v in votes.values()), "missing reviews"

db = sqlite3.connect(R / "../../.hunch/store.sqlite")
jev = {r[0]: {"outcome": r[1], "claims_done": r[2]} for r in db.execute(
    "select o.id, o.outcome, c.claims_done from outcome o join claims c using (id)")}


def wilson(k, n, z=1.96):
    p, d = k / n, 1 + z * z / n
    c, h = p + z * z / (2 * n), z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return (c - h) / d, (c + h) / d


def fleiss(rows, cats):  # rows: list of 3-label lists
    n, N = 3, len(rows)
    P = [(sum(c * c for c in Counter(r).values()) - n) / (n * (n - 1)) for r in rows]
    p = [sum(r.count(c) for r in rows) / (N * n) for c in cats]
    pe = sum(x * x for x in p)
    return (sum(P) / N - pe) / (1 - pe)


for field, cats in [("outcome", ["worked", "failed", "redirected", "unclear"]), ("claims_done", ["yes", "no"])]:
    labels = {n: [votes[n][r][field] for r in REVIEWERS] for n in ids}
    decided = {n: Counter(l).most_common(1)[0][0] for n, l in labels.items() if Counter(l).most_common(1)[0][1] >= 2}
    unanimous = sum(len(set(l)) == 1 for l in labels.values())
    k = sum(jev[ids[n]][field] == m for n, m in decided.items())
    lo, hi = wilson(k, len(decided))
    print(f"\n{field}: Jev matches the reviewer majority on {k}/{len(decided)} decided rows ({k/len(decided):.0%}, "
          f"95% CI {lo:.0%}–{hi:.0%}); {len(ids) - len(decided)} no majority; unanimous {unanimous}/{len(ids)}; "
          f"Fleiss kappa {fleiss(list(labels.values()), cats):.2f}")
    conf = Counter((m, jev[ids[n]][field]) for n, m in decided.items() if jev[ids[n]][field] != m)
    for (m, j), c in conf.most_common():
        print(f"  panel {m:<10} Jev {j:<10} ×{c}")
    print("  panel majority:", dict(Counter(decided.values())), " Jev on the same rows:",
          dict(Counter(jev[ids[n]][field] for n in decided)))
