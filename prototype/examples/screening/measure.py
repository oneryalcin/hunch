"""The numbers of the cookbook: how much reading hunch saves in title-and-abstract screening, and what it costs.

    uv run python prototype/examples/screening/measure.py      # after fetch.py and the runs; asks nothing

For each review, papers are read in order of hunch's p(yes), highest first. It reports how much of the pile a person
reads before finding 95% of the papers the reviewers kept at screening, the work saved (the field's WSS@95: the
share left unread, minus the 5% allowed to be missed), and how many papers that ended up in the review are among
those missed. With 95% intervals from 1,000 bootstrap resamples of the papers, and split by whether a paper has an
abstract; how much a person reads to reach every paper that ended up in the review; and the same for active
learning (ASReview, from baseline.py's saved reading orders, three runs), when they exist. Then a bar chosen fairly:
the lowest confidence for "no" that keeps recall >= 95% on one half of the papers (split by a hash of the id),
checked on the other half.
"""
import csv
import hashlib
import json
import os
from pathlib import Path

import numpy as np

os.environ["HUNCH_MAX_COST"] = "0"
import hunch  # noqa: E402
from hunch.core import decide  # noqa: E402

HERE = Path(__file__).parent
REVIEWS = {"anxiety": "van_Dis_2019", "diet_risk": "Moran_2020", "wilson": "Appenzeller-Herzog_2019"}
RECALL, B = 0.95, 1000


def p_yes(spec: Path) -> dict[str, float]:
    res = next(iter(hunch.execute(hunch.load(spec)).values()))
    out = {}
    for it in res["items"]:
        label, p = decide(res["answers"][it["key"]])[:2]
        out[it["id"]] = p if label == "yes" else 1 - p
    return out


def read_for(y, s, recall=RECALL):
    """Share of the papers read, highest p(yes) first, to find this share of the kept ones."""
    o = np.argsort(-s, kind="stable")
    k = np.searchsorted(np.cumsum(y[o]), recall * y.sum())
    return (k + 1) / len(y), o[: k + 1]


def recall_below(y, pno, m, bar):
    """Share of the kept papers in m that a person still reads when a "no" at least this sure is set aside."""
    return (y[m] * (pno[m] < bar)).sum() / y[m].sum()


def main() -> None:
    rng = np.random.default_rng(0)
    for name, review in REVIEWS.items():
        rows = list(csv.DictReader(open(HERE / f".cache/{review}.csv")))
        p = p_yes(HERE / f"{name}.yml")
        y = np.array([r["gold"] == "yes" for r in rows], float)
        inc = np.array([r["included"] == "yes" for r in rows], float)
        has = np.array([bool(r["abstract"]) for r in rows])
        s = np.array([p[r["id"]] for r in rows])
        read, top = read_for(y, s)
        missed_inc = int(inc.sum() - inc[top].sum())
        boot = [read_for(y[i], s[i])[0] for i in (rng.choice(len(y), len(y)) for _ in range(B))]
        lo, hi = np.percentile(boot, [2.5, 97.5])
        print(f"\n{name} ({review}): {len(y):,} papers, {int(y.sum())} kept at screening, {int(inc.sum())} in the review")
        print(f"  to find 95% of the kept papers, read {read:.1%} ({lo:.1%}–{hi:.1%}): work saved {1 - read - (1 - RECALL):.1%}; "
              f"{missed_inc} of {int(inc.sum())} papers in the review are among those not read")
        print(f"  to reach every paper in the review, read {read_for(inc, s, 1.0)[0]:.1%}")
        al = sorted((HERE / ".cache" / "asreview").glob(f"{review}_s*.json"))
        if al:
            pos = {r["id"]: i for i, r in enumerate(rows)}
            runs = []
            for f in al:  # papers it never reached come last
                seen = [pos[i] for i in json.load(open(f))]
                done = set(seen)
                rest = [i for i in range(len(rows)) if i not in done]
                o = np.array(seen + rest)
                runs.append([read_for(y[o], -np.arange(len(o)))[0], read_for(inc[o], -np.arange(len(o)), 1.0)[0]])
            a = np.array(runs)
            print(f"  active learning ({len(al)} runs): 95% of the kept papers after {a[:, 0].min():.1%}–{a[:, 0].max():.1%}, "
                  f"every paper in the review after {a[:, 1].min():.1%}–{a[:, 1].max():.1%}")
        for label, m in (("with an abstract", has), ("title only", ~has)):
            if y[m].sum():
                print(f"  {label:17} {m.sum():>6,} papers, {int(y[m].sum()):>4} kept: to find 95% read {read_for(y[m], s[m])[0]:.1%}")
        # a bar for "no" chosen on half the papers, checked on the other half
        half = np.array([int(hashlib.sha256(r["id"].encode()).hexdigest(), 16) % 2 for r in rows]) == 0
        pno = np.where(s < 0.5, 1 - s, -1.0)
        bars = sorted({round(v, 3) for v in pno if v >= 0.5})
        bar = next((b for b in bars if recall_below(y, pno, half, b) >= RECALL), None)
        if bar is not None:
            m = ~half
            print(f"  act for no chosen on half the papers: {bar}; on the other half recall {recall_below(y, pno, m, bar):.1%} of "
                  f"{int(y[m].sum())} kept, reading {(pno[m] < bar).mean():.1%}; papers in the review set aside: "
                  f"{int((inc[m] * (pno[m] >= bar)).sum())} of {int(inc[m].sum())}")


if __name__ == "__main__":
    main()
