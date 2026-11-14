"""What else could a distilled student learn from? Experiments of design rounds 24 and 26 (docs/04-design.md).

Each changes one thing about today's student (mdbr-leaf-mt, one linear layer per question) and scores it on answers
the store already holds. Nothing here asks an engine.

  round 26
    frozen b77|guard CONFIG...                       the encoder stays as it is; CONFIG is encoders:head:targets
        encoders   one of hunch's, or several joined by + (their numbers side by side)
        head       linear (hunch's layer) | mlp-1e-4 | mlp-1e-3 | mlp-1e-2 (a hidden layer of 256, by weight decay)
        targets    hard (the teacher's answer, as today) | soft (its probabilities) | half (the mean of the two)
    finetune b77|guard answers|both|time EPOCHS LR [SEED...]    the encoder's own weights train too (PyTorch)
  round 24
    rules b77|guard ENCODER...      a bar chosen from the data in place of act 0.9: undrgrnd-labs/hunch's two rules.
                                    `rules b77` at 97% is the encoder table's "answered locally at matched quality"
    exposure ENCODER...             has an encoder seen BANKING77's own labels?
  Round 24's other measurements are not here: they need encoders hunch does not ship, EmbeddingGemma 2 in PyTorch
  (its layers, its prefix) or scikit-learn.

b77: each of BANKING77's 3,000 unlabelled messages is answered by a student trained without its fifth and compared
with Jev; a student trained on all of them is graded on the 385 holdout messages against reviewed gold.
guard: the command guard's three questions, every command answered by a student trained without its fifth. The first
student listed is the reference: the others are also scored on as many of their surest answers as it had at 0.9.

Needs Jev's stored answers for prototype/examples/banking77/intent.yml on banking77_unlabeled.csv and
banking77_holdout.csv, and for prototype/examples/claude_code/command_guard.yml (its sessions: NOTICE.md there).
Embeddings are kept in prototype/.cache/ (or $EXP_CACHE). Measured on 2026-11-13 with the scratch scripts this file
was merged from, and again from this file the next day with the same numbers (the 10-epoch fine-tuning within 0.1
point): from the repo root, Apple M1 Pro, as

    HUNCH_STORE=<a copy of the store> PYTHONPATH=src \
        uv run --no-project --python 3.12 --with numpy --with fastembed==0.8.0 --with pyyaml --with httpx \
        python prototype/distill_experiments.py frozen b77 mdbr-leaf-mt:linear:hard mdbr-leaf-mt:linear:soft

(numpy 2.5.4, onnxruntime 1.31.1; $FASTEMBED_CACHE_PATH named a folder that already held the encoders). For finetune,
`--with torch --with sentence-transformers` added (torch 2.15.0, sentence-transformers 6.2.0) and
TOKENIZERS_PARALLELISM=false PYTORCH_ENABLE_MPS_FALLBACK=1 set, with $HF_HOME naming where PyTorch's copy of the
encoder was kept.
"""
import hashlib
import os
import sys
import time
from pathlib import Path

os.environ["HUNCH_MAX_COST"] = "0"
import numpy as np  # noqa: E402

from hunch import distill as D  # noqa: E402
from hunch.answers import attach_gold, load_reviews  # noqa: E402
from hunch.execute import execute  # noqa: E402
from hunch.spec import load_project  # noqa: E402

REPO = Path(__file__).resolve().parents[1]
B77 = REPO / "prototype/examples/banking77/intent.yml"
GUARD = REPO / "prototype/examples/claude_code/command_guard.yml"
CACHE = Path(os.environ.get("EXP_CACHE") or REPO / "prototype/.cache/distill_experiments")
CLIP = 3000


# ---- the stored answers ----

def b77_items(source):
    project = load_project(B77)
    project["nodes"]["intent"]["source"] = (B77.parent / source).resolve()
    res = execute(project, dry=True)["intent"]
    attach_gold(res["items"], load_reviews(res["spec"]))
    missing = sum(it["key"] not in res["answers"] for it in res["items"])
    if missing:
        raise SystemExit(f"{source}: {missing} rows have no stored answer. `hunch run` the spec on it first (that asks Jev).")
    return res


def b77_rows(source):
    """One BANKING77 file: (text, Jev's stored answer, reviewed gold or None, which fifth, by the text's hash)."""
    res = b77_items(source)
    return [(D.text_of(it["state"]), res["answers"][it["key"]], set(it["gold"]) if it["gold"] else None,
             int(it["shash"], 16) % 5) for it in res["items"]]


def b77_data():
    """The 3,000 lessons and the 385 holdout messages, Jev's answer to each lesson as a class, and the three targets."""
    lessons, hold = b77_rows("banking77_unlabeled.csv"), b77_rows("banking77_holdout.csv")
    classes = sorted({a["choice"] for _, a, _, _ in lessons})
    y = np.array([classes.index(a["choice"]) for _, a, _, _ in lessons])
    hard = np.eye(len(classes))[y]
    soft = np.array([[a["probabilities"].get(c, 0.0) for c in classes] for _, a, _, _ in lessons])
    soft /= soft.sum(1, keepdims=True)
    return lessons, hold, classes, y, np.array([r[3] for r in lessons]), {"hard": hard, "soft": soft, "half": (hard + soft) / 2}


def clip(text):
    """A long command keeps its head and tail (two thirds, one third), so every encoder reads the same text."""
    if len(text) <= CLIP:
        return text
    head = CLIP * 2 // 3
    return text[:head] + "\n…\n" + text[-(CLIP - head):]


def guard_rows():
    """Per question: (clipped text, the label distill trains on, has a panel verdict, which fifth, Jev's p(yes))."""
    res = execute(load_project(GUARD), dry=True)["command_guard"]
    attach_gold(res["items"], load_reviews(res["spec"]))
    by_q = {}
    for it in res["items"]:
        a = res["answers"].get(it["key"])
        if not a:
            continue
        teacher = D.label_of(a)
        lab = teacher if not it["gold"] or teacher in D.gold_classes(it) else sorted(D.gold_classes(it))[0]
        by_q.setdefault(it["qid"], []).append((clip(D.text_of(it["state"])), lab, bool(it["gold"]), int(it["shash"], 16) % 5, a["noul"]))
    if not by_q:
        raise SystemExit("command_guard: no stored answers. `hunch run` the spec first (that asks Jev).")
    return by_q


def feats(names, texts):
    """Each text's embedding from one of hunch's encoders, or from several side by side, scaled back to unit length."""
    out = []
    for name in names.split("+"):
        f = CACHE / f"{name}_{len(texts)}_{hashlib.sha256(chr(0).join(texts).encode()).hexdigest()[:8]}.npy"
        if not f.exists():
            CACHE.mkdir(parents=True, exist_ok=True)
            t = time.time()
            np.save(f, D.encode(name, texts))
            print(f"  [{name}] embedded {len(texts)} texts in {time.time() - t:.0f}s", file=sys.stderr, flush=True)
        out.append(np.load(f))
    return np.concatenate(out, 1) / np.float32(np.sqrt(len(out)))


# ---- scoring ----

def softmax(Z):
    P = np.exp(Z - Z.max(1, keepdims=True))
    return P / P.sum(1, keepdims=True)


def temp_of(Z, y):
    """hunch's rule (D.temperature) on logits already out of fold: one temperature by NLL, 1 below MIN_MISSES mistakes."""
    if int((Z.argmax(1) != y).sum()) < D.MIN_MISSES:
        return 1.0
    grid = np.exp(np.linspace(np.log(0.25), np.log(4), 200))

    def nll(t):
        L = Z / t - (Z / t).max(1, keepdims=True)
        return -(L[np.arange(len(y)), y] - np.log(np.exp(L).sum(1))).mean()
    return float(grid[np.argmin([nll(t) for t in grid])])


def auroc(pos, neg):
    """P(a random pos scores above a random neg)."""
    allv = np.concatenate([pos, neg])
    ranks = allv.argsort().argsort() + 1.0
    for v in np.unique(allv):  # average ranks on ties
        m = allv == v
        ranks[m] = ranks[m].mean()
    return (ranks[:len(pos)].sum() - len(pos) * (len(pos) + 1) / 2) / (len(pos) * len(neg))


def ece(conf, right, bins=10):
    which = np.clip(np.digitize(conf, np.linspace(0, 1, bins + 1)[1:-1]), 0, bins - 1)
    return float(sum(abs(conf[which == i].mean() - right[which == i].mean()) * (which == i).mean() for i in range(bins) if (which == i).any()))


def matched(conf, agree, target=0.97):
    """The largest share answered locally whose answers still agree with Jev at `target`. Plain agreement: the
    encoder table's "matched quality" (`symmetric`) asks the low end of a Wilson interval to clear it, and is lower."""
    order = np.argsort(-conf)
    ok = np.nonzero(np.cumsum(agree[order]) / np.arange(1, len(order) + 1) >= target)[0]
    return (ok[-1] + 1) / len(order) if len(ok) else 0.0


def b77_line(name, Z, Zh, y, classes, hold):
    """Z: logits for the 3,000 lessons, each from a student trained without it. Zh: for the 385 holdout messages."""
    T = temp_of(Z, y)
    P, Ph = softmax(Z / T), softmax(Zh / T)
    agree, conf = P.argmax(1) == y, P.max(1)
    right = np.array([classes[i] in g for i, (_, _, g, _) in zip(Ph.argmax(1), hold)])
    jev = np.array([a["choice"] in g for _, a, g, _ in hold])
    sel = Ph.max(1) >= .9
    print(f"  {name:36s} T={T:.2f} | out of fold: agrees with Jev {agree.mean():.1%}, local at 0.9 {(conf >= .9).mean():.1%} "
          f"(disagree {(~agree[conf >= .9]).mean():.1%}), local at 97% agreement {matched(conf, agree):.1%} | holdout: alone {right.mean():.1%}, "
          f"calibration error {ece(Ph.max(1), right):.3f} ({ece(softmax(Zh).max(1), right):.3f} without the temperature), "
          f"local at 0.9 {sel.sum()} ({sel.mean():.1%}) with {(~right[sel]).sum()} wrong, with Jev behind {np.where(sel, right, jev).mean():.1%}", flush=True)


def guard_line(name, per_q, base):
    """per_q: (question, p(yes) for each command, whether its label is yes). `base` keeps how many answers the first
    student gave at 0.9; each student is also scored on that many of its surest no and surest yes answers, so
    students that are confident more or less often are compared at the same share of work kept."""
    tot = {k: 0 for k in ("yes", "miss", "alarm", "local", "n", "same_miss", "same_alarm")}
    aurocs = []
    for qid, pyes, yes in per_q:
        a, b = base.setdefault(qid, (int((pyes <= .1).sum()), int((pyes >= .9).sum())))
        order = np.argsort(pyes)
        tot["yes"] += yes.sum()
        tot["miss"] += (yes & (pyes <= .1)).sum()
        tot["alarm"] += (~yes & (pyes >= .9)).sum()
        tot["local"] += ((pyes <= .1) | (pyes >= .9)).sum()
        tot["n"] += len(yes)
        tot["same_miss"] += yes[order[:a]].sum()
        tot["same_alarm"] += (~yes[order[len(order) - b:]]).sum()
        aurocs.append(auroc(pyes[yes], pyes[~yes]))
    print(f"  {name:36s} | at 0.9: confident misses {tot['miss']} of {tot['yes']}, false alarms {tot['alarm']}, local {tot['local'] / tot['n']:.1%} | "
          f"at the first student's local share: misses {tot['same_miss']}, false alarms {tot['same_alarm']} | AUROC {' '.join(f'{a:.3f}' for a in aurocs)}", flush=True)


# ---- frozen encoder: other targets, another head, more encoders ----

def linear(X, Y, y, C=8.0, steps=800):
    """D.fit with the targets as rows of probabilities. Rows are weighted by the class of `y`, as D.fit weighs them."""
    n, d = X.shape
    k = Y.shape[1]
    l2 = 1 / (C * n)
    counts = np.bincount(y, minlength=k).astype(np.float32)
    rw = (n / (k * np.maximum(counts, 1)))[y][:, None].astype(np.float32)
    W, b = np.zeros((d, k), np.float32), np.zeros(k, np.float32)
    Y = Y.astype(np.float32)
    mW, vW, mb, vb = (np.zeros_like(W), np.zeros_like(W), np.zeros_like(b), np.zeros_like(b))
    for t in range(1, steps + 1):
        Z = X @ W + b
        P = np.exp(Z - Z.max(1, keepdims=True))
        P /= P.sum(1, keepdims=True)
        G = (P - Y) * rw / n
        gW, gb = X.T @ G + l2 * W, G.sum(0)
        for p, g, m, v in ((W, gW, mW, vW), (b, gb, mb, vb)):
            m[:] = 0.9 * m + 0.1 * g
            v[:] = 0.999 * v + 0.001 * g * g
            p -= 0.05 * (m / (1 - 0.9 ** t)) / (np.sqrt(v / (1 - 0.999 ** t)) + 1e-8)
    return lambda Xn: Xn @ W + b


def mlp(wd, hidden=256, steps=200, lr=1e-3, seed=0):
    """One hidden layer (ReLU), cross-entropy weighted as hunch weighs it, L2 on both weight matrices, Adam."""
    def fit(X, Y, y):
        rng = np.random.default_rng(seed)
        n, d = X.shape
        k = Y.shape[1]
        s = np.float32(np.sqrt(d))  # unit-length rows have entries near 1/sqrt(d): bring them to about 1
        Xs = X.astype(np.float32) * s
        counts = np.bincount(y, minlength=k).astype(np.float32)
        rw = (n / (k * np.maximum(counts, 1)))[y][:, None].astype(np.float32)
        P = [rng.normal(0, np.sqrt(2 / d), (d, hidden)).astype(np.float32), np.zeros(hidden, np.float32),
             np.zeros((hidden, k), np.float32), np.zeros(k, np.float32)]
        M, V = [np.zeros_like(p) for p in P], [np.zeros_like(p) for p in P]
        Yf = Y.astype(np.float32)
        for t in range(1, steps + 1):
            H = np.maximum(Xs @ P[0] + P[1], 0)
            Z = H @ P[2] + P[3]
            Q = np.exp(Z - Z.max(1, keepdims=True))
            Q /= Q.sum(1, keepdims=True)
            G = (Q - Yf) * rw / n
            dH = (G @ P[2].T) * (H > 0)
            grads = [Xs.T @ dH + wd * P[0], dH.sum(0), H.T @ G + wd * P[2], G.sum(0)]
            for p, g, m, v in zip(P, grads, M, V):
                m[:] = 0.9 * m + 0.1 * g
                v[:] = 0.999 * v + 0.001 * g * g
                p -= lr * (m / (1 - 0.9 ** t)) / (np.sqrt(v / (1 - 0.999 ** t)) + 1e-8)
        return lambda Xn: np.maximum(Xn.astype(np.float32) * s @ P[0] + P[1], 0) @ P[2] + P[3]
    return fit


HEADS = {"linear": linear, "mlp-1e-4": mlp(1e-4), "mlp-1e-3": mlp(1e-3), "mlp-1e-2": mlp(1e-2)}


def oof(fit, X, Y, y, folds):
    Z = np.zeros(Y.shape, np.float32)
    for f in range(5):
        tr = folds != f
        Z[~tr] = fit(X[tr], Y[tr], y[tr])(X[~tr])
    return Z


def frozen_b77(configs):
    lessons, hold, classes, y, folds, targets = b77_data()
    texts, htexts = [r[0] for r in lessons], [r[0] for r in hold]
    jev = np.array([max(a["probabilities"].values()) for _, a, _, _ in lessons])
    print(f"BANKING77: {len(lessons)} lessons, {len(classes)} intents, {len(hold)} holdout messages. Jev's own confidence on the lessons: "
          f"median {np.median(jev):.2f}, below 0.9 on {(jev < .9).mean():.1%}. Jev alone on the holdout: "
          f"{np.mean([a['choice'] in g for _, a, g, _ in hold]):.1%}", flush=True)
    X = feats(D.DEFAULT, texts)
    W, b = D.fit(X, y, len(classes))
    print(f"  `linear` on the teacher's answers against hunch's own D.fit, {D.DEFAULT}: largest difference between their logits "
          f"{np.abs(linear(X, targets['hard'], y)(X) - (X @ W + b)).max():.1e}", flush=True)
    for c in configs:
        names, head, target = c.split(":")
        X, Xh, Y = feats(names, texts), feats(names, htexts), targets[target]
        b77_line(c, oof(HEADS[head], X, Y, y, folds), HEADS[head](X, Y, y)(Xh), y, classes, hold)


def frozen_guard(configs):
    by_q = guard_rows()
    texts = sorted({r[0] for rows in by_q.values() for r in rows})
    pos = {t: i for i, t in enumerate(texts)}
    unsure = np.concatenate([[r[4] for r in rows if not r[2]] for rows in by_q.values()])
    print(f"command guard: {len(texts)} commands in {len(next(iter(by_q.values())))} rows a question, "
          f"{sum(r[1] == 'yes' for rows in by_q.values() for r in rows)} yes answers. On rows without a panel verdict Jev's p(yes) "
          f"is between 0.1 and 0.9 on {((unsure > .1) & (unsure < .9)).mean():.1%}", flush=True)
    base = {}
    for c in configs:
        names, head, target = c.split(":")
        E = feats(names, texts)
        per_q = []
        for qid, rows in by_q.items():
            X = np.stack([E[pos[r[0]]] for r in rows])
            y = np.array([int(r[1] == "yes") for r in rows])
            gold, jev = np.array([r[2] for r in rows]), np.array([r[4] for r in rows])
            hard = np.eye(2)[y]
            soft = np.where(gold[:, None], hard, np.stack([1 - jev, jev], 1))  # a panel verdict is the truth: it stays hard
            Z = oof(HEADS[head], X, {"hard": hard, "soft": soft, "half": (hard + soft) / 2}[target], y, np.array([r[3] for r in rows]))
            per_q.append((qid, softmax(Z / temp_of(Z, y))[:, 1], y == 1))
        guard_line(c, per_q, base)


# ---- round 24: a bar chosen from the data, and what an encoder already knew ----

def wilson(k, m, z=1.96):
    if m == 0:
        return 0.0, 1.0
    ph, d = k / m, 1 + z * z / m
    c, h = (ph + z * z / (2 * m)) / d, z * ((ph * (1 - ph) / m + z * z / (4 * m * m)) ** .5) / d
    return c - h, c + h


def symmetric(P, g, target, minority=0.80):
    """undrgrnd-labs/hunch's rule for a question's bar: the lowest confidence, on a 0.01 grid from 0.5, whose answers
    agree with the teacher at `target` and, among answers other than the commonest, at `minority`; each counted at
    the low end of a 95% Wilson interval. The encoder table's "matched quality" is this at 0.97."""
    pred, cf = P.argmax(1), P.max(1)
    agree = pred == g
    minor = g != np.bincount(g, minlength=P.shape[1]).argmax()
    for t in np.arange(0.5, 0.995, 0.01):
        sel = cf >= t
        ms = sel & minor
        if (sel.sum() >= 30 and wilson(int(agree[sel].sum()), int(sel.sum()))[0] >= target
                and ms.sum() >= 15 and wilson(int(agree[ms].sum()), int(ms.sum()))[0] >= minority):
            return float(t)
    return None


def one_sided(pyes, g, recall=0.85, miss=0.02):
    """Their rule where yes is rare: the student answers only "no", where p(yes) is below tau. tau is the largest that
    keeps the high end of the interval on the yes answers it lets through under `miss`, and the low end on the share
    of yes answers it passes on above `recall`. None: the question stays with the teacher."""
    if int((g == 1).sum()) < 15:
        return None
    for tau in np.unique(np.round(pyes, 4))[::-1]:
        local = pyes < tau
        if (local.sum() >= 30 and wilson(int((g[~local] == 1).sum()), int((g == 1).sum()))[0] >= recall
                and wilson(int((g[local] == 1).sum()), int(local.sum()))[1] <= miss):
            return float(tau)
    return None


def rules_b77(names):
    lessons, _, classes, y, folds, targets = b77_data()
    texts = [r[0] for r in lessons]
    print("BANKING77, the 3,000 lessons out of fold against Jev. At each target: the bar `symmetric` picks, the share "
          "answered at it (how often those agree)", flush=True)
    for name in names:
        Z = oof(linear, feats(name, texts), targets["hard"], y, folds).astype(np.float64)
        P = softmax(Z / temp_of(Z, y))
        agree, cf = P.argmax(1) == y, P.max(1)
        out = f"  {name:20s} | agrees {agree.mean():.1%} | act 0.9: {(cf >= .9).mean():.1%} ({(~agree[cf >= .9]).mean():.2%} disagree) |"
        for target in (0.90, 0.95, 0.97):
            t = symmetric(P, y, target)
            sel = cf >= (t if t is not None else 2)
            out += f" {target:.0%}: " + (f"{t:.2f}, {sel.mean():.1%} ({agree[sel].mean():.1%})" if t is not None else "none") + " |"
        loc = agr = 0  # the bar chosen without one fifth, scored on that fifth
        for f in range(5):
            t = symmetric(P[folds != f], y[folds != f], 0.95)
            sel = (folds == f) & (cf >= (t if t is not None else 2))
            loc, agr = loc + sel.sum(), agr + agree[sel].sum()
        print(out + f" 95%, each fifth at a bar chosen without it: {loc / len(y):.1%} ({agr / max(1, loc):.1%})", flush=True)


def rules_guard(names):
    by_q = guard_rows()
    texts = sorted({r[0] for rows in by_q.values() for r in rows})
    pos = {t: i for i, t in enumerate(texts)}
    print("command guard, out of fold: act 0.9 both ways, then `one_sided`", flush=True)
    for name in names:
        E = feats(name, texts)
        tot = {k: 0 for k in ("yes", "act", "miss", "local", "fold_miss", "fold_local", "n")}
        for qid, rows in by_q.items():
            X = np.stack([E[pos[r[0]]] for r in rows])
            y = np.array([int(r[1] == "yes") for r in rows])
            folds = np.array([r[3] for r in rows])
            Z = oof(linear, X, np.eye(2)[y], y, folds).astype(np.float64)
            pyes = softmax(Z / temp_of(Z, y))[:, 1]
            yes = y == 1
            tau = one_sided(pyes, y)
            local = pyes < (tau if tau is not None else -1)
            fold_local = np.zeros(len(y), bool)  # tau chosen without one fifth, scored on that fifth
            for f in range(5):
                tf = one_sided(pyes[folds != f], y[folds != f])
                fold_local |= (folds == f) & (pyes < (tf if tf is not None else -1))
            for k, v in (("yes", yes), ("act", yes & (pyes <= .1)), ("miss", yes & local), ("local", local),
                         ("fold_miss", yes & fold_local), ("fold_local", fold_local), ("n", np.ones(len(y), bool))):
                tot[k] += int(v.sum())
            print(f"  {name:20s} {qid:16s} yes {yes.sum():3d} | AUROC {auroc(pyes[yes], pyes[~yes]):.3f} | act 0.9: lets through "
                  f"{(yes & (pyes <= .1)).sum()}, local {((pyes <= .1) | (pyes >= .9)).mean():.1%} | one-sided: "
                  + (f"tau {tau:.3f}, local {local.mean():.1%}, lets through {(yes & local).sum()}" if tau is not None else "stays with the teacher")
                  + f" | each fifth at a tau chosen without it: local {fold_local.mean():.1%}, lets through {(yes & fold_local).sum()}", flush=True)
        print(f"  {name:20s} all three: act 0.9 lets through {tot['act']} of {tot['yes']}; one-sided {tot['miss']}, answering "
              f"{tot['local'] / tot['n']:.1%} of answers; each fifth at a tau chosen without it {tot['fold_miss']}, answering "
              f"{tot['fold_local'] / tot['n']:.1%}", flush=True)


def exposure(names):
    """Would an encoder that saw BANKING77 in training be flattered here? Two checks, neither conclusive. Where Jev
    and the dataset's own label differ, does a student trained only on Jev's answers side with the dataset? And
    how far do two, then five, of Jev's answers per intent take it on the holdout?"""
    lessons, hold, classes, y, _, _ = b77_data()
    texts, htexts = [r[0] for r in lessons], [r[0] for r in hold]
    raw = [it["raw_gold"] if isinstance(it["raw_gold"], str) else sorted(it["raw_gold"])[0]
           for it in b77_items("banking77_holdout.csv")["items"]]
    jev = [a["choice"] for _, a, _, _ in hold]
    print(f"BANKING77 holdout: Jev gives the dataset's own label on {np.mean([j == g for j, g in zip(jev, raw)]):.1%}", flush=True)
    for name in names:
        X, Xh = feats(name, texts), feats(name, htexts)
        W, b = D.fit(X, y, len(classes))
        lab = [classes[i] for i in (Xh @ W + b).argmax(1)]
        differ = [(l, j, g) for l, j, g in zip(lab, jev, raw) if j != g]
        print(f"  {name}: {np.mean([l in g for l, (_, _, g, _) in zip(lab, hold)]):.1%} against reviewed gold, "
              f"{np.mean([l == g for l, g in zip(lab, raw)]):.1%} against the dataset's own labels, agrees with Jev on "
              f"{np.mean([l == j for l, j in zip(lab, jev)]):.1%}; where Jev and the dataset differ ({len(differ)} rows) it sides with "
              f"the dataset on {sum(l == g for l, j, g in differ)}, with Jev on {sum(l == j for l, j, g in differ)}, "
              f"with neither on {sum(l != g and l != j for l, j, g in differ)}", flush=True)
        rng = np.random.default_rng(0)
        for per in (2, 5):
            idx = np.concatenate([rng.permutation(np.nonzero(y == c)[0])[:per] for c in range(len(classes))])
            Wf, bf = D.fit(X[idx], y[idx], len(classes))
            few = [classes[i] for i in (Xh @ Wf + bf).argmax(1)]
            print(f"      trained on {per} of Jev's answers per intent ({len(idx)} rows): "
                  f"{np.mean([l in g for l, (_, _, g, _) in zip(few, hold)]):.1%} against reviewed gold", flush=True)


# ---- the encoder trains too ----

def encoder():
    """mdbr-leaf-mt in PyTorch, at hunch's pinned revision and token limit, and its weights to start every run from."""
    import torch
    from sentence_transformers import SentenceTransformer
    spec = D.FILES["mdbr-leaf-mt"]
    st = SentenceTransformer(spec["repo"], revision=spec["revision"], device="mps" if torch.backends.mps.is_available() else "cpu")
    st.max_seq_length = spec["max_tokens"]
    return st, {k: v.detach().clone() for k, v in st.state_dict().items()}


def forward(st, texts):
    import torch
    batch = {k: v.to(st.device) if torch.is_tensor(v) else v for k, v in st.tokenize(texts).items()}
    return torch.nn.functional.normalize(st(batch)["sentence_embedding"], dim=-1)


def embed(st, texts, bs=64):
    import torch
    st.eval()
    order = np.argsort([len(t) for t in texts])  # similar lengths together: less padding
    out = np.zeros((len(texts), 1024), np.float32)
    with torch.no_grad():
        for i in range(0, len(texts), bs):
            idx = order[i:i + bs]
            out[idx] = forward(st, [texts[j] for j in idx]).float().cpu().numpy()
    return out


def train(st, state0, texts, tasks, predict, epochs, lr, seed=0, bs=32, log=None, bucket=False):
    """One run, starting from today's student: the encoder as published and hunch's fitted layers.
    tasks: per question (targets [n, k] as probabilities, each row's class for its weight or -1 where the row has
    no answer, hunch's fitted (W, b)). Returns each question's logits for every list of texts in `predict`.
    AdamW, the layers at ten times the encoder's rate, a tenth of the steps warming up and then a straight line to
    zero, gradients clipped at 1; the loss is hunch's (D.fit), without its L2."""
    import torch
    dev = st.device
    st.load_state_dict(state0)
    torch.manual_seed(seed)
    rng = np.random.default_rng(seed)
    heads, targets, weights = [], [], []
    for Y, y, (W, b) in tasks:
        k = Y.shape[1]
        head = torch.nn.Linear(W.shape[0], k).to(dev)
        head.weight.data, head.bias.data = torch.tensor(W.T.copy()).to(dev), torch.tensor(b.copy()).to(dev)
        has = y >= 0
        counts = np.bincount(y[has], minlength=k).astype(np.float32)
        w = np.where(has, (has.sum() / (k * np.maximum(counts, 1)))[np.maximum(y, 0)], 0).astype(np.float32)  # hunch's row weights
        heads.append(head)
        targets.append(torch.tensor(Y, dtype=torch.float32))
        weights.append((torch.tensor(w), int(has.sum())))
    params = [{"params": st.parameters(), "lr": lr, "weight_decay": 0.01},
              {"params": [p for h in heads for p in h.parameters()], "lr": lr * 10, "weight_decay": 0.0}]
    opt = torch.optim.AdamW(params)
    steps = epochs * int(np.ceil(len(texts) / bs))
    warm = max(1, steps // 10)
    sched = torch.optim.lr_scheduler.LambdaLR(opt, lambda s: (s + 1) / warm if s < warm else max(0.0, (steps - s) / max(1, steps - warm)))
    t0 = time.time()
    for epoch in range(epochs):
        st.train()
        order, total = rng.permutation(len(texts)), 0.0
        if bucket:  # long texts: batches of similar length, in random order, so little is spent on padding
            order = order[np.argsort([len(texts[j]) for j in order], kind="stable")]
        batches = [order[i:i + bs] for i in range(0, len(order), bs)]
        for bi in (rng.permutation(len(batches)) if bucket else range(len(batches))):
            idx = batches[bi]
            emb = forward(st, [texts[j] for j in idx])
            loss = 0
            for head, Y, (w, n) in zip(heads, targets, weights):
                ce = -(Y[idx].to(dev) * torch.nn.functional.log_softmax(head(emb), -1)).sum(1)
                loss = loss + (ce * w[idx].to(dev)).sum() / n * len(texts) / len(idx)  # weighted mean over the question's rows
            opt.zero_grad()
            loss.backward()
            torch.nn.utils.clip_grad_norm_([p for g in params for p in g["params"]], 1.0)
            opt.step()
            sched.step()
            total += float(loss.detach()) * len(idx)
            if bucket and dev.type == "mps":  # every new batch shape adds to the GPU backend's buffer cache: after three
                torch.mps.empty_cache()       # epochs of the guard's long texts the machine paged and the run stalled
        if log:
            print(f"      {log} epoch {epoch + 1}/{epochs}: loss {total / len(texts):.4f}, {time.time() - t0:.0f}s", flush=True)
    out = []
    for group in predict:
        X = embed(st, group)
        out.append([X @ h.weight.detach().float().cpu().numpy().T + h.bias.detach().float().cpu().numpy() for h in heads])
    return out


def finetune_b77(mode, epochs, lr, seeds):
    lessons, hold, classes, y, folds, targets = b77_data()
    texts, htexts = [r[0] for r in lessons], [r[0] for r in hold]
    X, Xh = feats(D.DEFAULT, texts), feats(D.DEFAULT, htexts)
    st, state0 = encoder()
    cos = (embed(st, texts[:200]) * X[:200]).sum(1)
    print(f"device {st.device}; the PyTorch encoder against hunch's ONNX one on 200 lessons: cosine {cos.min():.5f} to {cos.max():.5f}", flush=True)
    k = len(classes)
    Z0 = np.zeros((len(y), k), np.float32)
    for f in range(5):
        W, b = D.fit(X[folds != f], y[folds != f], k)
        Z0[folds == f] = X[folds == f] @ W + b
    W, b = D.fit(X, y, k)
    b77_line("today (frozen encoder)", Z0, Xh @ W + b, y, classes, hold)
    if mode == "time":  # one fold, to see what a run costs before the full protocol
        tr = folds != 0
        t = time.time()
        train(st, state0, [texts[i] for i in np.nonzero(tr)[0]], [(targets["hard"][tr], y[tr], D.fit(X[tr], y[tr], k))],
              [[texts[i] for i in np.nonzero(~tr)[0]]], epochs, lr, log="fold 0")
        print(f"  one fold of {tr.sum()} lessons, {epochs} epochs: {time.time() - t:.0f}s")
        return
    for tname, Y in (("answers", targets["hard"]), ("probabilities", targets["soft"])) if mode == "both" else (("answers", targets["hard"]),):
        for seed in seeds:
            t = time.time()
            Z = np.zeros((len(y), k), np.float32)
            for f in range(5):
                tr = folds != f
                Z[~tr] = train(st, state0, [texts[i] for i in np.nonzero(tr)[0]], [(Y[tr], y[tr], D.fit(X[tr], y[tr], k))],
                               [[texts[i] for i in np.nonzero(~tr)[0]]], epochs, lr, seed)[0][0]
            Zh = train(st, state0, texts, [(Y, y, (W, b))], [htexts], epochs, lr, seed)[0][0]
            b77_line(f"trained on {tname}, lr {lr:g}, {epochs} ep, seed {seed}", Z, Zh, y, classes, hold)
            print(f"      ({time.time() - t:.0f}s for six trainings)", flush=True)


def finetune_guard(mode, epochs, lr, seeds):
    """One encoder for the three questions, a layer each. Works on the distinct commands (a command in several rows
    counts once), where frozen_guard counts every row, so the two references differ a little."""
    by_q = guard_rows()
    texts = sorted({r[0] for rows in by_q.values() for r in rows})
    pos = {t: i for i, t in enumerate(texts)}
    E = feats(D.DEFAULT, texts)
    n, qids = len(texts), list(by_q)
    y = np.full((len(qids), n), -1)
    fold = np.zeros(n, int)
    for qi, qid in enumerate(qids):
        for text, lab, _, f, _ in by_q[qid]:
            y[qi, pos[text]], fold[pos[text]] = int(lab == "yes"), f
    st, state0 = encoder()
    cos = (embed(st, texts[:100]) * E[:100]).sum(1)
    print(f"command guard: {n} commands, {int((y == 1).sum())} yes answers; device {st.device}; "
          f"the PyTorch encoder against hunch's ONNX one on 100 commands: cosine {cos.min():.5f} to {cos.max():.5f}", flush=True)
    base = {}

    def report(name, Z):  # Z[qi]: [n, 2] out-of-fold logits
        per_q = []
        for qi, qid in enumerate(qids):
            has = y[qi] >= 0
            per_q.append((qid, softmax(Z[qi][has] / temp_of(Z[qi][has], y[qi][has]))[:, 1], y[qi][has] == 1))
        guard_line(name, per_q, base)

    def start(tr):  # hunch's fitted layer per question, on the frozen numbers of the training rows
        return [D.fit(E[tr & (y[qi] >= 0)], y[qi][tr & (y[qi] >= 0)], 2) for qi in range(len(qids))]

    Z0 = [np.zeros((n, 2), np.float32) for _ in qids]
    for f in range(5):
        for qi, (W, b) in enumerate(start(fold != f)):
            Z0[qi][fold == f] = E[fold == f] @ W + b
    report("today (frozen encoder)", Z0)
    for seed in seeds:
        t = time.time()
        Z = [np.zeros((n, 2), np.float32) for _ in qids]
        for f in range(5):
            tr = fold != f
            tasks = [(np.eye(2, dtype=np.float32)[np.maximum(y[qi][tr], 0)], y[qi][tr], wb) for qi, wb in enumerate(start(tr))]
            got = train(st, state0, [texts[i] for i in np.nonzero(tr)[0]], tasks, [[texts[i] for i in np.nonzero(~tr)[0]]],
                        epochs, lr, seed, bs=16, log=f"fold {f}", bucket=True)[0]
            for qi in range(len(qids)):
                Z[qi][~tr] = got[qi]
            if mode == "time":
                print(f"  one fold of {tr.sum()} commands, {epochs} epochs: {time.time() - t:.0f}s")
                return
        report(f"encoder trained, lr {lr:g}, {epochs} ep, seed {seed}", Z)
        print(f"      ({time.time() - t:.0f}s for five trainings, one encoder for the three questions)", flush=True)


if __name__ == "__main__":
    what, data, *rest = sys.argv[1:]
    if what == "frozen":
        {"b77": frozen_b77, "guard": frozen_guard}[data](rest)
    elif what == "rules":
        {"b77": rules_b77, "guard": rules_guard}[data](rest)
    elif what == "exposure":
        exposure([data, *rest])
    else:
        mode, epochs, lr, *seeds = rest
        {"b77": finetune_b77, "guard": finetune_guard}[data](mode, int(epochs), float(lr), [int(s) for s in seeds] or [0])
