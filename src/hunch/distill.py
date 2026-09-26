"""hunch distill: a small local model per question, trained on the answers the spec's engine already gave.

The store holds every answer the engine gave, keyed by its exact input. `hunch distill SPEC` embeds each row's
state with a small frozen encoder (MiniLM, via fastembed: ONNX on CPU) and fits one linear layer per question: to
your gold where a row has it (an answer key or a review), else to the engine's answer. A temperature, fitted
on five-fold held-out predictions, makes its confidence an honest probability. A fixed random fifth of
the gold rows (by a hash of the row's text) is held out, and the model records the rows it trained on, so
`hunch test SPEC --model distilled:…` grades it only on rows it never saw. (Leaving all gold out would bias it:
review queues put flagged rows first, so gold holds most of the rare answers.)

The result is an engine like any other: `model: distilled:<folder>`, with `escalate: {model: jev-1.13.0}` so every
answer below `act` is answered by the teacher instead. It answers only the questions it was trained on, word for word: change a
question and it refuses until you distill again. Needs the `distill` extra: `uv add "hunch-ai[distill]"`.
"""
import hashlib
import json
import os
from pathlib import Path

ENCODERS = {"minilm": "sentence-transformers/all-MiniLM-L6-v2"}
MIN_ROWS = 20  # per question: fewer and there is nothing to learn from
RARE = 30  # fewer examples of an answer than this and the student rarely learns to give it
_encoders: dict = {}
_models: dict = {}


def text_of(state) -> str:
    """What the encoder reads: the state as sent, columns in order."""
    return state if isinstance(state, str) else " | ".join(f"{k}: {v}" for k, v in state.items())


def require() -> None:
    """numpy and fastembed come with the extra; say so instead of a ModuleNotFoundError."""
    try:
        import fastembed  # noqa: F401
        import numpy  # noqa: F401
    except ImportError:
        raise SystemExit('distilled models need the distill extra: uv add "hunch-ai[distill]"') from None


def encode(name: str, texts: list[str]):
    import numpy as np
    from fastembed import TextEmbedding
    if name not in _encoders:  # a stable cache: fastembed's default is the temp folder, which the OS clears
        cache = os.environ.get("FASTEMBED_CACHE_PATH") or str(Path.home() / ".cache" / "hunch" / "encoders")
        _encoders[name] = TextEmbedding(ENCODERS[name], cache_dir=cache)
    return np.array(list(_encoders[name].embed(texts)), dtype=np.float32)


def fit(X, y, k: int, C: float = 8.0, steps: int = 800):
    """Softmax regression by full-batch Adam: (W, b). A linear layer on frozen embeddings, nothing more. The L2
    penalty is scikit-learn's LogisticRegression(C=8) per row: on BANKING77 it matched it to the row (90.6%,
    and the same share confident enough to answer alone), where 30x more regularization left it never sure.
    Classes weigh equally (rows weighted by 1/their class's count): on the command guard, where 2% of commands
    are a yes, a plain fit learned to say no, and missed 14 of 29 real sends_out yeses where balanced missed 6."""
    import numpy as np
    n, d = X.shape
    l2 = 1 / (C * n)
    counts = np.bincount(y, minlength=k).astype(np.float32)
    rw = (n / (k * np.maximum(counts, 1)))[y][:, None].astype(np.float32)
    W, b = np.zeros((d, k), np.float32), np.zeros(k, np.float32)
    Y = np.eye(k, dtype=np.float32)[y]
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
    return W, b


def probs(W, b, X):
    import numpy as np
    Z = X @ W + b
    P = np.exp(Z - Z.max(1, keepdims=True))
    return P / P.sum(1, keepdims=True)


def temperature(X, y, k: int, folds) -> float:
    """One number that makes the student's confidence an honest probability (temperature scaling): fit on
    predictions for rows each fold's model never saw, so it measures the student on new rows, not on what it
    memorised. On BANKING77 the plain student was underconfident (calibration error 0.118 on the holdout); T=0.55
    brought it to 0.048, with the same answers."""
    import numpy as np
    Z = np.zeros((len(y), k), np.float32)
    for f in set(folds.tolist()):
        tr = folds != f
        if len(set(y[tr].tolist())) < 2:  # too few rows to hold any out: leave the confidence as it is
            return 1.0
        W, b = fit(X[tr], y[tr], k)
        Z[~tr] = X[~tr] @ W + b

    def nll(t):
        L = Z / t
        L = L - L.max(1, keepdims=True)
        return -(L[np.arange(len(y)), y] - np.log(np.exp(L).sum(1))).mean()
    grid = np.exp(np.linspace(np.log(0.05), np.log(20), 300))
    return round(float(grid[np.argmin([nll(t) for t in grid])]), 4)


def question_digest(aq: dict) -> str:
    return hashlib.sha256(json.dumps(aq, ensure_ascii=False, sort_keys=True).encode()).hexdigest()[:16]


def label_of(a: dict) -> str:
    """The class a teacher's answer trains: yes/no, the option, or the score level's number."""
    if a["type"] == "noul":
        return "yes" if a["noul"] >= 0.5 else "no"
    if a["type"] == "choice":
        return a["choice"]
    return str(round(a["score"]))


def held_out(it: dict) -> bool:
    """A fifth of the gold rows, fixed by the row's text: never trained on, so a test can grade them."""
    return int(it["shash"], 16) % 5 == 0


def gold_classes(it: dict) -> set[str]:
    """The item's gold in the classes the model learns (a score's level as its number)."""
    return {g.split(":", 1)[0] for g in it["gold"]} if it["q"]["type"] == "score" else set(it["gold"])


def ungrade_trained(spec: dict, items: list[dict]) -> int:
    """Under a distilled engine, drop the gold of rows the model trained on, so a test grades only unseen rows."""
    path = spec["_dir"] / spec["model"].split(":", 1)[1] / "meta.json"  # the meta alone: no numpy needed to grade
    seen = set(json.loads(path.read_text()).get("trained", [])) if path.exists() else set()
    n = 0
    for it in items:
        if it["gold"] and it["shash"] in seen:  # as if it had no gold: every estimator skips it
            it["gold"], it["gold_src"], it["raw_gold"], n = None, None, None, n + 1
    return n


def distill(project: dict, node: str, encoder: str = "minilm") -> tuple[Path, list[str]]:
    """Train on the store's answers for `node` over its source rows (or --traffic); ask nothing. Returns the model
    folder and one report line per question."""
    require()
    import numpy as np

    from hunch import core
    res = core.execute(project, dry=True)[node]  # dry: the answers already in the store, nothing asked
    spec = res["spec"]
    core.attach_gold(res["items"], core.load_reviews(spec))
    by_q: dict[str, list] = {}
    trained = set()
    for it in res["items"]:
        a = res["answers"].get(it["key"])
        if not a or (it["gold"] and held_out(it)):
            continue
        teacher = label_of(a)  # gold where known (the teacher's answer if gold accepts it), else the teacher
        lab = teacher if not it["gold"] or teacher in gold_classes(it) else sorted(gold_classes(it))[0]
        by_q.setdefault(it["qid"], []).append((text_of(it["state"]), lab, it["aq"], int(it["shash"], 16) % 5))
        trained.add(it["shash"])
    if not by_q:
        raise SystemExit(f"{node}: no answers in the store to learn from; `hunch run` first (or --traffic)")
    texts = sorted({r[0] for rows in by_q.values() for r in rows})
    X = dict(zip(texts, encode(encoder, texts)))
    arrays, report = {}, []
    meta = {"encoder": encoder, "teacher": spec["model"], "questions": {}, "trained": sorted(trained)}
    for qid, rows in by_q.items():
        classes = sorted({r[1] for r in rows})
        if len(rows) < MIN_ROWS or len(classes) < 2:
            report.append(f"  {qid}: skipped ({len(rows)} answers, {len(classes)} distinct; needs {MIN_ROWS}+ and 2+)")
            continue
        aq = rows[0][2]
        Xq, yq = np.stack([X[r[0]] for r in rows]), np.array([classes.index(r[1]) for r in rows])
        W, b = fit(Xq, yq, len(classes))
        arrays[f"{qid}.W"], arrays[f"{qid}.b"] = W, b
        meta["questions"][qid] = {"type": aq["type"], "question": question_digest(aq), "classes": classes,
                                  "legend": list(aq.get("criteria") or []) if aq["type"] == "score" else None,
                                  "trained_on": len(rows),
                                  "temperature": temperature(Xq, yq, len(classes), np.array([r[3] for r in rows]))}
        rare = min((sum(r[1] == c for r in rows), c) for c in classes)
        report.append(f"  {qid}: {len(rows)} answers, {len(classes)} classes; rarest: {rare[1]!r} ({rare[0]})"
                      + ("  ← few examples: measure what it misses (hunch test --model) before trusting it"
                         if rare[0] < RARE else ""))
    if not meta["questions"]:
        raise SystemExit(f"{node}: nothing to distill\n" + "\n".join(report))
    blob = json.dumps(meta, sort_keys=True).encode() + b"".join(arrays[k].tobytes() for k in sorted(arrays))
    out = spec["_dir"] / "distilled" / f"{node}@{hashlib.sha256(blob).hexdigest()[:8]}"
    out.mkdir(parents=True, exist_ok=True)
    np.savez(out / "weights.npz", **arrays)
    (out / "meta.json").write_text(json.dumps(meta, indent=2) + "\n")
    return out, report


def load(spec: dict, model: str) -> tuple[dict, dict]:
    require()
    import numpy as np
    path = (spec["_dir"] / model.split(":", 1)[1]).resolve()
    if path not in _models:
        if not (path / "meta.json").exists():
            raise SystemExit(f"{model}: no distilled model at {path}; `hunch distill` writes one")
        w = np.load(path / "weights.npz")
        _models[path] = (json.loads((path / "meta.json").read_text()), {k: w[k] for k in w.files})
    return _models[path]


def answer(spec: dict, model: str, items: list[dict]) -> dict[str, dict]:
    """{key: answer} in the engine's own shapes, for items of questions this model was trained on, word for word."""
    meta, w = load(spec, model)
    for it in items:
        m = meta["questions"].get(it["qid"])
        if m is None or m["question"] != question_digest(it["aq"]):
            raise SystemExit(f"{model}: trained without question {it['qid']!r} as it is worded now; "
                             f"`hunch distill` again (the model answers only the questions it learned)")
    texts = sorted({text_of(it["state"]) for it in items})
    X = dict(zip(texts, encode(meta["encoder"], texts)))
    out = {}
    for it in items:
        m = meta["questions"][it["qid"]]
        t = m.get("temperature", 1.0)  # models distilled before 0.3 have none
        p = dict(zip(m["classes"], probs(w[f"{it['qid']}.W"] / t, w[f"{it['qid']}.b"] / t, X[text_of(it["state"])][None])[0].tolist()))
        p = {k: round(v, 4) for k, v in p.items()}
        if m["type"] == "noul":
            out[it["key"]] = {"type": "noul", "noul": p.get("yes", 0.0)}
        elif m["type"] == "choice":
            best = max(p, key=p.get)
            out[it["key"]] = {"type": "choice", "choice": best, "confidence": p[best], "probabilities": p}
        else:
            levels = {str(i): p.get(str(i), 0.0) for i in range(len(m["legend"]))}
            best = max(levels, key=levels.get)
            out[it["key"]] = {"type": "score", "score": round(sum(int(k) * v for k, v in levels.items()), 4),
                              "confidence": levels[best], "legend": {str(i): lab for i, lab in enumerate(m["legend"])},
                              "probabilities": levels}
    return out
