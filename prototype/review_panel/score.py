"""Score blind reviews: neutral estimate of Jev and answer-key accuracy on the BANKING77 holdout,
reviewer agreement, and a check of the unverified claims_fixed column."""
import csv, json, math
from collections import Counter
from pathlib import Path

R = Path(__file__).parent
REVIEWERS = ["opus", "sonnet_a", "sonnet_b"]
N_AGREE_TOTAL, N_HOLDOUT = 340, 385


def wilson(k, n, z=1.96):
    if n == 0:
        return (0, 1)
    p = k / n
    d = 1 + z * z / n
    c = p + z * z / (2 * n)
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return ((c - h) / d, (c + h) / d)


def fleiss_binary(votes):  # votes: list of lists of bools (one list per item)
    n = len(votes[0]); N = len(votes)
    P = [(sum(v) * (sum(v) - 1) + (n - sum(v)) * (n - sum(v) - 1)) / (n * (n - 1)) for v in votes]
    pbar = sum(P) / N
    p1 = sum(sum(v) for v in votes) / (N * n)
    pe = p1 ** 2 + (1 - p1) ** 2
    return (pbar - pe) / (1 - pe) if pe < 1 else 1.0


# ---------- BANKING77 ----------
m = json.load(open(R / "key/b77_map.json"))
by_id = {}
for rev in REVIEWERS:
    for a in json.load(open(R / f"answers/b77_{rev}.json")):
        info = m[f"{rev}:{a['n']}"]
        c = a["candidates"]
        rec = by_id.setdefault(info["id"], {**info, "gold_ok": [], "jev_ok": [], "best": []})
        rec["gold_ok"].append(bool(c.get(info["gold"])))
        rec["jev_ok"].append(bool(c.get(info["jev"])))
        rec["best"].append(a["best"])
rows = list(by_id.values())
assert all(len(r["gold_ok"]) == 3 for r in rows), "missing reviews"
maj = lambda v: sum(v) >= 2
for r in rows:
    r["g"], r["j"] = maj(r["gold_ok"]), maj(r["jev_ok"])

dis = [r for r in rows if r["kind"] == "disagree"]
agr = [r for r in rows if r["kind"] == "agree"]
v = Counter(("jev only" if r["j"] and not r["g"] else "key only" if r["g"] and not r["j"] else "both ok" if r["j"] else "neither") for r in dis)
print(f"BANKING77 holdout (v3), 3 blind reviewers, majority vote")
print(f"  {len(dis)} disagreements (Jev ≠ answer key):")
for k in ["jev only", "key only", "both ok", "neither"]:
    print(f"    {k:<9} {v[k]:>3}")
bad = sum(not r["g"] for r in agr)
lo, hi = wilson(bad, len(agr))
print(f"  audit slice: {len(agr)} random rows where Jev = answer key; label NOT acceptable in {bad} ({bad/len(agr):.1%}, 95% CI {lo:.1%}–{hi:.1%})")


def est(ok_dis):
    point = (N_AGREE_TOTAL * (1 - bad / len(agr)) + ok_dis) / N_HOLDOUT
    return point, (N_AGREE_TOTAL * (1 - hi) + ok_dis) / N_HOLDOUT, (N_AGREE_TOTAL * (1 - lo) + ok_dis) / N_HOLDOUT


for name, ok in [("Jev", sum(r["j"] for r in dis)), ("answer key", sum(r["g"] for r in dis))]:
    p, l, h = est(ok)
    print(f"  estimated {name:<10} accuracy (label acceptable): {p:.1%} (95% CI {l:.1%}–{h:.1%})")

votes = [r["gold_ok"] for r in rows] + [r["jev_ok"] for r in dis]
pair = sum(r["gold_ok"][i] == r["gold_ok"][j] for r in rows for i, j in [(0, 1), (0, 2), (1, 2)]) / (3 * len(rows))
print(f"  reviewer agreement: Fleiss kappa {fleiss_binary(votes):.2f}; pairwise agreement on answer-key label {pair:.1%}")
print(f"  unanimous verdicts on disagreements: {sum(len(set(r['gold_ok']))==1 and len(set(r['jev_ok']))==1 for r in dis)}/{len(dis)}")

# my earlier (non-blind) verdicts vs the panel
mine = {r["row_id"]: r["verdict"] for r in csv.DictReader(open(R.parent / "examples/banking77/intent.reviews.csv"))}
panel = {r["id"]: ("model_right" if r["j"] and not r["g"] else "key_right" if r["g"] and not r["j"] else "ambiguous" if r["j"] else "neither") for r in dis}
same = [i for i in mine if panel.get(i) == mine[i]]
print(f"  Claude's earlier verdicts vs panel: {len(same)}/{len(mine)} match")
for i in mine:
    if panel.get(i) != mine[i]:
        print(f"    #{i}: Claude said {mine[i]}, panel says {panel.get(i)}")

# ---------- SWE claims_fixed ----------
m2 = json.load(open(R / "key/swe_map.json"))
sw = {}
for rev in REVIEWERS:
    for a in json.load(open(R / f"answers/swe_{rev}.json")):
        info = m2[f"{rev}:{a['n']}"]
        sw.setdefault(info["id"], {**info, "votes": []})["votes"].append(a["claims_fixed"])
srows = list(sw.values())
for r in srows:
    c = Counter(r["votes"]).most_common(1)[0]
    r["maj"] = c[0] if c[1] >= 2 else "split"
unan = sum(len(set(r["votes"])) == 1 for r in srows)
agree = [r for r in srows if r["maj"] in ("yes", "no")]
jev_ok = sum(r["jev"] == r["maj"] for r in agree)
print(f"\nSWE claims_fixed, 40 random traces, 3 blind reviewers")
print(f"  reviewers unanimous on {unan}/40; majority label: {Counter(r['maj'] for r in srows)}")
print(f"  Jev matches reviewer majority on {jev_ok}/{len(agree)} ({jev_ok/len(agree):.0%})")
for r in agree:
    if r["jev"] != r["maj"]:
        print(f"    {r['id']}: Jev {r['jev']} (p={r['jev_pyes']:.2f}), reviewers {r['votes']}")
claims = [r for r in srows if r["maj"] == "yes"]
print(f"  reviewer-majority 'claims fixed': {len(claims)}; of those tests fail: {sum(r['resolved']=='no' for r in claims)}")
