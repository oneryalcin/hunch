"""What an answer means: routing on confidence, gold and reviews, and the statistics that score answers."""

import csv
import hashlib
import itertools
import math
import random
from pathlib import Path

from hunch.spec import Unknown

REVIEW_FIELDS = ["qid", "row_id", "state_hash", "verdict", "label", "reviewer", "at", "kind"]


def decide(a: dict) -> tuple[str, float, float]:
    """(label, confidence in that label, margin from the decision boundary)."""
    if a["type"] == "noul":
        p = a["noul"]
        return ("yes" if p >= 0.5 else "no"), max(p, 1 - p), abs(p - 0.5) * 2
    if a["type"] == "choice":
        ps = sorted(a["probabilities"].values(), reverse=True)
        return a["choice"], ps[0], ps[0] - (ps[1] if len(ps) > 1 else 0)
    s = a["score"]  # score: nearest level
    level = str(round(s))
    return f"{level}:{a['legend'][level]}", a["confidence"], 1 - abs(s - round(s)) * 2


def ranked(a: dict) -> list[tuple[str, float]]:
    if a["type"] == "noul":
        return sorted([("yes", a["noul"]), ("no", 1 - a["noul"])], key=lambda x: -x[1])
    if a["type"] == "choice":
        return sorted(a["probabilities"].items(), key=lambda x: -x[1])
    return sorted(((f"{k}:{a['legend'][k]}", v) for k, v in a["probabilities"].items()), key=lambda x: -x[1])


def act_needed(q: dict, label: str) -> float | None:
    """Confidence needed to act on this label. `act: 0.9`, or for yes/no `act: {yes: 0.95, no: 0.8}`:
    a judge's "no" and "yes" are rarely equally reliable (SWE-agent: p(yes) < 0.2 was right 52/55)."""
    act = q.get("act")
    if act is None:
        return None
    if isinstance(act, dict):
        return act[label]
    return act


def route(q: dict, a: dict, path_p: float = 1.0) -> str:
    label, conf, _ = decide(a)
    need = act_needed(q, label)
    return "" if need is None else ("act" if conf * path_p >= need else "review")


def conf_of(it: dict, a: dict) -> float:
    """The confidence hunch acts on. For a `chain: true` judgment (a refinement: its answer can only be right if
    the row was routed to it correctly) this is its own confidence times P(it was routed correctly); otherwise
    (a filter only decides *whether* to ask, e.g. "check the fix only where the agent claims one") it is just
    its own confidence. BANKING77 tree: chaining lifts how well confidence separates right from wrong answers
    (AUROC 0.79 → 0.88)."""
    return decide(a)[1] * it.get("path_p", 1.0)


def p_where(pred, row: dict, answers: dict[str, dict | None]) -> float:
    """P(the where-clause holds) under the upstream answers' full distributions, not just their top labels:
    `where: claim != 'no_claim'` holds with probability p(claimed_fixed) + p(unclear)."""
    if any(a is None for a in answers.values()):
        return 1.0  # dry run: answer not cached yet
    total = 0.0
    for combo in itertools.product(*[[(qid, lab, p) for lab, p in ranked(a)] for qid, a in answers.items()]):
        r2, pr = dict(row), 1.0
        for qid, lab, p in combo:
            r2[qid], pr = lab, pr * p
        try:
            total += pr if pred(r2) else 0.0
        except Unknown:
            pass
    return total


def show(a: dict) -> str:
    label, conf, _ = decide(a)
    return f"{label} {conf:.2f}"


def is_yes(v: str) -> bool:
    return v.strip().lower() in {"1", "true", "yes", "y"}


def sign_test(fixed: int, broke: int) -> float:
    """Two-sided exact sign test on paired flips: is 'fixed vs broken' distinguishable from a coin toss?"""
    n, k = fixed + broke, min(fixed, broke)
    return 1.0 if n == 0 else min(1.0, 2 * sum(math.comb(n, i) for i in range(k + 1)) / 2**n)


def auroc(pos: list[float], neg: list[float]) -> float:
    """Chance a random positive scores above a random negative (ties count half): the Mann-Whitney U statistic
    from ranks, O(n log n) (the pairwise version took 150 s at 100k rows)."""
    both = sorted([(v, 1) for v in pos] + [(v, 0) for v in neg])
    rank_sum, i = 0.0, 0
    while i < len(both):
        j = i
        while j < len(both) and both[j][0] == both[i][0]:
            j += 1
        rank_sum += (i + 1 + j) / 2 * sum(b[1] for b in both[i:j])  # tied block: its average rank
        i = j
    return (rank_sum - len(pos) * (len(pos) + 1) / 2) / (len(pos) * len(neg))


def calibration(pairs: list[tuple], bins: int = 10) -> tuple[float, list[tuple]]:
    """Expected calibration error over equal-width bins, and the reliability table.
    pairs: (stated p, happened) or (stated p, happened, weight); weights re-create a population's base rate."""
    pairs = [(p[0], p[1], p[2] if len(p) > 2 else 1.0) for p in pairs]
    total = sum(w for _, _, w in pairs)
    table, ece = [], 0.0
    for b in range(bins):
        lo, hi = b / bins, (b + 1) / bins
        sel = [(p, h, w) for p, h, w in pairs if lo <= p < hi or (b == bins - 1 and p == hi)]
        if sel:
            ws = sum(w for _, _, w in sel)
            conf, acc = sum(p * w for p, _, w in sel) / ws, sum(h * w for _, h, w in sel) / ws
            ece += ws / total * abs(acc - conf)
            table.append((lo, hi, len(sel), conf, acc))
    return ece, table


def wilson(k: float, n: float, z: float = 1.96) -> tuple[float, float]:
    if n == 0:
        return 0.0, 1.0
    p, d = k / n, 1 + z * z / n
    c, h = p + z * z / (2 * n), z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return (c - h) / d, (c + h) / d


def reviews_path(spec: dict) -> Path:
    """Default: next to the spec. `reviews: path` shares verdicts between judgments that ask the same question
    of the same rows (gold belongs to the data, not to one spec)."""
    return spec["_dir"] / spec.get("reviews", f"{spec['judgment']}.reviews.csv")


def load_reviews(spec: dict) -> dict[tuple[str, str], dict]:
    """Human verdicts, last one wins. A file next to the spec: reviewed like code, never lost with the cache."""
    path = reviews_path(spec)
    if not path.exists():
        return {}
    with open(path, newline="") as f:
        return {(r["qid"], r["row_id"]): r for r in csv.DictReader(f)}


def append_review(spec: dict, rec: dict) -> None:
    path = reviews_path(spec)
    new = not path.exists()
    with open(path, "a", newline="") as f:
        w = csv.DictWriter(f, fieldnames=REVIEW_FIELDS, lineterminator="\n")
        if new:
            w.writeheader()
        w.writerow(rec)


def normalize_gold(q: dict, v: str) -> frozenset | None:
    """Gold is a set of acceptable labels: taxonomies overlap (BANKING77: 20 of 45 disagreements had two
    defensible labels), so "right" means "in the set". Source columns give one label; reviews can give more."""
    v = (v or "").strip()
    if not v:
        return None
    if "_multi" in q:  # the parent's gold is the set of options that apply; "-" = none of them
        return frozenset({"yes" if q["_multi"][1] in {s.strip() for s in v.split("|")} else "no"})
    if q["type"] == "score":  # a level: "2", "2:Frustrated" or "Frustrated" all mean level 2
        levels = [str(c).strip().lower() for c in q.get("criteria") or []]
        return frozenset({str(levels.index(v.lower())) if v.lower() in levels else v.split(":", 1)[0].strip()})
    return frozenset({("yes" if is_yes(v) else "no") if q["type"] == "noul" else v})


def gold_str(g: frozenset | None) -> str:
    return "|".join(sorted(g)) if g else "-"


def hit(it: dict, a: dict, gold: str = "gold") -> bool:
    if a["type"] == "score":  # answers are "level:text"; gold is a level, from a column or a review
        return decide(a)[0].split(":", 1)[0] in {g.split(":", 1)[0] for g in it[gold]}
    return decide(a)[0] in it[gold]


EXCLUDED = ("ambiguous", "needs_context")  # verdicts that drop a row from scoring


def attach_gold(items: list[dict], reviews: dict) -> None:
    """Effective gold = a review verdict on this exact row text if there is one, else the source column.
    Verdicts: model_right / key_right / labeled / confirmed / against_right / spec_right → that label; both_ok → both labels;
    ambiguous / needs_context (a reviewer could only decide it by knowing more than the state shows) → row dropped
    from scoring. A verdict follows its text: matched by row id, else by the exact state it was made on (ids can
    shift, e.g. when the trace reader stops counting a kind of message); on text that has since changed, ignored."""
    by_text = {(r["qid"], r["state_hash"]): r for r in reviews.values()}
    text_of = {(it["qid"], it["id"]): it["shash"] for it in items}
    ids_with = {}
    for it in items:
        ids_with.setdefault((it["qid"], it["shash"]), []).append(it["id"])
    for it in items:
        col = it["q"].get("gold")
        it["raw_gold"] = normalize_gold(it["q"], it["row"].get(col, "")) if col else None
        r, copied = reviews.get((it["qid"], it["id"])), False
        if not r or r["state_hash"] != it["shash"]:
            r = by_text.get((it["qid"], it["shash"]))
            # One row owns a verdict: the row it was made on, if that row still has the reviewed text, else (the id
            # shifted) the first live row with that text. Other rows with the same text get the verdict as gold, but
            # were not drawn at random, so they must not count as spot checks (they would narrow the interval).
            if r:
                owner = r["row_id"] if text_of.get((r["qid"], r["row_id"])) == r["state_hash"] \
                    else min(ids_with[(it["qid"], it["shash"])])
                copied = it["id"] != owner
        it["verdict"] = r["verdict"] if r else None
        it["review_kind"] = ("same_text" if copied else r.get("kind") or "") if it["verdict"] else None
        if it["verdict"] in EXCLUDED:
            it["gold"], it["gold_src"] = None, "excluded"
        elif it["verdict"]:
            it["gold"], it["gold_src"] = frozenset(r["label"].split("|")), "review"
        else:
            it["gold"], it["gold_src"] = it["raw_gold"], ("source" if it["raw_gold"] else None)


def permuted(aq: dict, k: int) -> dict:
    """k=1: reversed options; k>1: seeded shuffle. Same meaning, different order."""
    opts = list(aq["criteria"].items())
    if k == 1:
        opts.reverse()
    else:
        random.Random(k).shuffle(opts)
    return {**aq, "criteria": dict(opts)}


def sample(its: list[dict], n: int) -> list[dict]:
    """Deterministic sample: stable across runs so its answers stay cached."""
    return sorted(its, key=lambda it: hashlib.sha256(it["id"].encode()).hexdigest())[:n]


def accuracy(items: list[dict], answers: dict, qid: str, gold: str = "gold") -> float | None:
    its = [it for it in items if it["qid"] == qid and it[gold]]
    return sum(hit(it, answers[it["key"]], gold) for it in its) / len(its) if its else None


def calib_pairs(its: list[dict], answers: dict, gold: str = "gold", weights: dict | None = None) -> list[tuple]:
    """(stated probability, did it happen, weight). choice/score: confidence vs answer in gold; noul: p(yes) vs "yes"."""
    out = []
    for it in its:
        a, g = answers[it["key"]], it[gold]
        if not g:
            continue
        w = weights.get(it["id"], 1.0) if weights else 1.0
        if a["type"] in ("choice", "score"):
            out.append((conf_of(it, a), hit(it, a, gold), w))
        elif a["type"] == "noul":
            out.append((a["noul"], "yes" in g, w))
    return out


def estimate_accuracy(its: list[dict], answers: dict, weights: dict | None = None) -> tuple[float, float, float, dict] | str:
    """Accuracy corrected by reviews, with a 95% interval. Rows are split by whether the model agreed with the
    source answer key: agreements are many (audit a random sample), disagreements few (review them all).
    Each group's reviewed rows estimate that group; groups are weighted by size. A fully reviewed group is exact.
    Returns a reason instead of an estimate unless agreements have an audit and *every* disagreement is reviewed:
    reviews made for another judgment (shared gold) are not a random sample of this one's disagreements
    (BANKING77 tree: the 24 it never had reviewed were almost all its own errors; extrapolating said 89.1%).
    With sampling weights (`_w`), groups are weighted by their share of total weight and reviewed rows by their
    own weight, so the estimate is for the population the weights describe."""
    w = (lambda it: weights[it["id"]]) if weights else (lambda it: 1.0)
    if not any(it["raw_gold"] for it in its):  # no answer key: gold only from reviews; random audits estimate all rows
        audits = [it for it in its if it["gold_src"] == "review" and it["review_kind"] == "audit"]
        if not audits:
            return "no answer key and no random audits yet (hunch review)"
        p, lo, hi, _ = wrate([(hit(it, answers[it["key"]]), w(it)) for it in audits])
        lo, hi = (p, p) if len(audits) >= len(its) else (lo, hi)
        return p, lo, hi, {"random": (len(audits), len(its))}
    groups: dict[str, list[dict]] = {"agree": [], "disagree": []}
    for it in its:
        if it["raw_gold"] and it["gold_src"] != "excluded":
            groups["agree" if hit(it, answers[it["key"]], "raw_gold") else "disagree"].append(it)
    total = sum(w(it) for members in groups.values() for it in members)
    est = lo = hi = 0.0
    detail = {}
    for name, members in groups.items():
        if not members:
            continue
        reviewed = [it for it in members if it["gold_src"] == "review"
                    and (name == "disagree" or it["review_kind"] == "audit")]  # agreements: random audits only
        detail[name] = (len(reviewed), len(members))
        if not reviewed:
            return f"no reviews of {name}ing rows yet"
        if name == "disagree" and len(reviewed) < len(members):
            return f"{len(members) - len(reviewed)} of {len(members)} disagreements unreviewed (hunch review --node …)"
        p, l, h, _ = wrate([(hit(it, answers[it["key"]]), w(it)) for it in reviewed])
        l, h = (p, p) if len(reviewed) >= len(members) else (l, h)
        share = sum(w(it) for it in members) / total
        est, lo, hi = est + share * p, lo + share * l, hi + share * h
    return est, lo, hi, detail


def wrate(fw: list[tuple[bool, float]]) -> tuple[float, float, float, float]:
    """Weighted share of True, its 95% Wilson interval on the effective sample size (Kish), and that size.
    Unit weights give k/n and wilson(k, n) exactly."""
    sw = sum(w for _, w in fw)
    if not sw:  # no rows, or none weighs anything (lint keeps population shares above 0)
        return 0.0, 0.0, 1.0, 0.0
    p, n_eff = sum(w for f, w in fw if f) / sw, sw * sw / sum(w * w for _, w in fw)
    lo, hi = wilson(p * n_eff, n_eff)
    return p, max(0.0, lo), min(1.0, hi), n_eff


def newcombe(a: tuple, b: tuple) -> tuple[float, float, float]:
    """a's rate minus b's, and its 95% interval (Newcombe's hybrid score, from each rate's (p, lo, hi))."""
    d = a[0] - b[0]
    return d, d - math.hypot(a[0] - a[1], b[2] - b[0]), d + math.hypot(a[2] - a[0], b[0] - b[1])
