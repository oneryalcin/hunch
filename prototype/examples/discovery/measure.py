"""The numbers of the cookbook: how much of what the lawyers wanted each reader finds, over all 455,449 messages.

    uv run --extra sql python prototype/examples/discovery/measure.py      # after fetch.py and the runs; asks nothing

Readers: a keyword search; the first-pass human reviewers (their calls before the senior lawyer settled appeals); the
question with the request's words only (request_only/); the question with the senior lawyer's reading (the specs
here). Each is scored against the final judgments, weighted by 1 / sampling probability, with 95% intervals from
1,000 bootstrap resamples within each stratum. Then: how much of the collection a reviewer reads, top of hunch's
ranking first, to find 80% of the relevant messages.
"""
import csv
import os
import re
from pathlib import Path

import numpy as np

os.environ["HUNCH_MAX_COST"] = "0"
import hunch  # noqa: E402
from hunch.core import decide  # noqa: E402

HERE = Path(__file__).parent
csv.field_size_limit(10**8)
TOPICS = {"drilling": "301", "spills": "302", "lobbying": "303", "privileged": "304"}
KEYWORDS = {  # a broad OR query per topic, the kind a reviewer would try first (drilling's needs "pipeline", which only
    # the lawyer's reading says is in scope)
    "drilling": r"drill|\boil\b|\bgas\b|extraction|pipeline|reserves|exploration",
    "spills": r"spill|blowout|leak|rupture|clean-?up|remediat",
    "lobbying": r"lobby|legislat|senator|congress|regulat|governor|\bbill\b|testimony|government affairs",
    "privileged": r"privilege|attorney|counsel|lawyer|legal|litigation|lawsuit|confidential"}
B = 1000


def p_yes(spec: Path) -> dict[str, float]:
    res = next(iter(hunch.execute(hunch.load(spec)).values()))
    out = {}
    for it in res["items"]:
        label, p = decide(res["answers"][it["key"]])[:2]
        out[it["id"]] = p if label == "yes" else 1 - p
    return out


def scores(y, w, pick):
    """Weighted recall, precision, F1 of the rows picked."""
    tp, found, rel = (w * y * pick).sum(), (w * pick).sum(), (w * y).sum()
    r, p = tp / rel, (tp / found if found else 0.0)
    return np.array([r, p, 2 * r * p / (r + p) if r + p else 0.0])


def read_for(y, w, s, recall):
    """Share of the collection read, highest p(yes) first, to reach this recall."""
    o = np.argsort(-s, kind="stable")
    hit = np.searchsorted(np.cumsum((w * y)[o]), recall * (w * y).sum())
    return w[o][: hit + 1].sum() / w.sum()


def main() -> None:
    first = {(t, m): r for t, _, m, r, _ in (line.split() for line in open(HERE / ".cache/qrels_pre.txt"))}
    rng = np.random.default_rng(0)
    for name, topic in TOPICS.items():
        rows = list(csv.DictReader(open(HERE / f".cache/{name}.csv")))
        y = np.array([r["gold"] == "yes" for r in rows], float)
        prob = np.array([float(r["stratum"]) for r in rows])
        w = 1 / prob
        request, protocol = p_yes(HERE / f"request_only/{name}.yml"), p_yes(HERE / f"{name}.yml")
        s = {k: np.array([d[r["id"]] for r in rows]) for k, d in (("request", request), ("protocol", protocol))}
        picks = {"keywords": np.array([bool(re.search(KEYWORDS[name], (r["email"] + r["attachments"]).lower()))
                                       for r in rows], float),
                 "first-pass reviewers": np.array([first[(topic, r["id"])] == "1" for r in rows], float),
                 "hunch, request only": (s["request"] >= 0.5).astype(float),
                 "hunch, lawyer's reading": (s["protocol"] >= 0.5).astype(float)}
        judged = np.array([first[(topic, r["id"])] in ("0", "1") for r in rows])  # first pass: -1 is unreadable
        strata = [np.flatnonzero(prob == v) for v in np.unique(prob)]
        boots = {k: [] for k in picks} | {"read": []}
        for _ in range(B):  # resample within each stratum; the weights stay the stratum's
            i = np.concatenate([rng.choice(ix, len(ix)) for ix in strata])
            for k, pk in picks.items():
                m = judged[i] if k == "first-pass reviewers" else np.ones(len(i), bool)
                boots[k].append(scores(y[i][m], w[i][m], pk[i][m]))
            boots["read"].append(read_for(y[i], w[i], s["protocol"][i], 0.8))
        print(f"\n{name} (topic {topic}): {(w * y).sum():,.0f} relevant of {w.sum():,.0f} messages")
        for k, pk in picks.items():
            m = judged if k == "first-pass reviewers" else np.ones(len(y), bool)
            est, lo, hi = scores(y[m], w[m], pk[m]), *np.percentile(boots[k], [2.5, 97.5], axis=0)
            print(f"  {k:24} " + "  ".join(f"{m} {e:.0%} ({a:.0%}–{b:.0%})" for m, e, a, b in
                                             zip(("recall", "precision", "F1"), est, lo, hi)))
        lo, hi = np.percentile(boots["read"], [2.5, 97.5])
        print(f"  to find 80%, read the top {read_for(y, w, s['protocol'], 0.8):.1%} ({lo:.1%}–{hi:.1%}) of the collection")


if __name__ == "__main__":
    main()
