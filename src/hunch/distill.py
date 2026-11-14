"""hunch distill: a small local model per question, trained on the answers the spec's engine already gave.

The store holds every answer the engine gave, keyed by its exact input. `hunch distill SPEC` embeds each row's
state with a small frozen encoder (mdbr-leaf-mt, ONNX on the CPU; --encoder names another, see FILES) and fits
one linear layer per question: to
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
import sys
from pathlib import Path

DEFAULT = "mdbr-leaf-mt"  # minilm through 0.4.0: this is the same size and speed, reads twice the tokens, a little more accurate
ENCODERS = {"mdbr-leaf-mt": "MongoDB/mdbr-leaf-mt", "minilm": "sentence-transformers/all-MiniLM-L6-v2",  # minilm: through fastembed
            "gte-modernbert-base": "Alibaba-NLP/gte-modernbert-base", "embeddinggemma-2": "onnx-community/embeddinggemma-2-ONNX"}
# Each encoder's files on Hugging Face, at a pinned revision (a student fits only the encoder it trained on), and
# how to feed them. Python loads these itself for the encoders fastembed doesn't carry (run_onnx), and a web page
# loads them for every encoder (hunch export). A build ships only if it computes the same embedding everywhere,
# checked on an Apple chip against the Intel code path (385 rows) and in a browser. Full precision did for
# mdbr-leaf-mt (92 MB) and gte-modernbert-base (596 MB); their 8-bit builds (23 and 150 MB) differed on 43 and 205
# of the rows. EmbeddingGemma 2's 8-bit build (314 MB) matched on every row.
# max_tokens: gte-modernbert-base takes 8,192, which on an M1 Pro's CPU is 31 s and 8 GB for one row; 2,048 is
# 2.3 s. EmbeddingGemma 2 takes 8,192 too and is cut at 1,024 (about 1 s).
# webgpu: a page uses it where the browser has it (student.js). In Chrome on an M1 Pro a short text took
# EmbeddingGemma 2 33 ms on WebGPU and 188 ms on WebAssembly (4 threads), gte-modernbert-base 24 ms and 193 ms
# (1 thread); MiniLM and mdbr-leaf-mt 11 ms and 7 ms.
FILES = {
    "minilm": {"repo": "Qdrant/all-MiniLM-L6-v2-onnx", "revision": "d13954661f83248295ba75c1ed411eef3b7b936e",
               "model": "model.onnx", "max_tokens": 256, "output": "last_hidden_state", "token_types": True},
    "mdbr-leaf-mt": {"repo": ENCODERS["mdbr-leaf-mt"], "revision": "1ed41b22ce166d66c24f88ebfc340e1f03adb20f",
                     "model": "onnx/model.onnx", "data": "onnx/model.onnx_data",
                     "max_tokens": 512, "output": "sentence_embedding", "token_types": True},
    "gte-modernbert-base": {"repo": ENCODERS["gte-modernbert-base"], "revision": "e7f32e3c00f91d699e8c43b53106206bcc72bb22",
                            "model": "onnx/model.onnx", "max_tokens": 2048, "output": "last_hidden_state", "pool": "cls",
                            "webgpu": True},
    "embeddinggemma-2": {"repo": ENCODERS["embeddinggemma-2"], "revision": "daa72c51243991dfcaf9f9137d2c573d8f7790c0",
                         "model": "onnx/model_quantized.onnx", "data": "onnx/model_quantized.onnx_data",
                         "max_tokens": 1024, "output": "sentence_embedding", "webgpu": True,
                         "empty": {f"{m}_features": [0, 512] for m in ("image", "video", "audio")}},
}
# Made-up rows an export carries with this machine's results, so the page can check it answers the same
# (student.js check()): plain, empty, a few sentences, and accents and emoji with a tab and both line endings. All
# short: a text at the token limit froze a tab for a minute where EmbeddingGemma 2 fell back to one-thread WebAssembly.
PROBES = ["hello", "", "Is this a question? It is.", "Café déjà vu 🚀 中文\ttab\r\nline two",
          "The package arrived on Tuesday, two days late. The box was open and one of the three items was missing. "
          "I would like a replacement or my money back, and I do not want to pay for shipping again."]
MIN_ROWS = 20  # per question: fewer and there is nothing to learn from
MIN_MISSES = 10  # held-out mistakes needed before the student's confidence is rescaled (see temperature)
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
    if name not in ENCODERS:
        raise SystemExit(f"encoder {name!r}: this hunch has {', '.join(ENCODERS)}")
    # a stable cache: fastembed's default is the temp folder, which the OS clears
    cache = os.environ.get("FASTEMBED_CACHE_PATH") or str(Path.home() / ".cache" / "hunch" / "encoders")
    if name != "minilm":
        return run_onnx(Path(cache), name, texts)
    from fastembed import TextEmbedding
    if name not in _encoders:
        _encoders[name] = TextEmbedding(ENCODERS[name], cache_dir=cache)
    return np.array(list(_encoders[name].embed(texts)), dtype=np.float32)


def run_onnx(cache: Path, name: str, texts: list[str]):
    """An encoder fastembed doesn't carry, on the runtime it brings (onnxruntime, tokenizers, huggingface_hub),
    fed as FILES says: one text at a time, up to its token limit, no task prefix, the graph's own sentence
    embedding (or its first token's state), normalised. (fastembed can't load EmbeddingGemma 2: its graph also
    takes image, video and audio.)
    Measured on BANKING77, 3,000 answers each scored by a student that had not trained on them:
    EmbeddingGemma 2's classification prefix kept fewer answers local (77.1% against 79.3% at act 0.9). Its 8-bit
    build gave the same students as full precision (cosine 0.9995 or more) at a third of the download; on an M1
    Pro it is the slower of the two (58 ms against 22 for a short message, about the same for a long one).
    mdbr-leaf-mt kept 73.1% local at the lowest threshold whose agreement with the teacher clears 97% at the low
    end of a 95% Wilson interval, MiniLM 68.2%."""
    import numpy as np
    spec = FILES[name]
    if name not in _encoders:
        import onnxruntime as ort
        from huggingface_hub import hf_hub_download
        from tokenizers import Tokenizer
        # plain files in one folder: onnxruntime refuses weights that resolve outside the model's own folder,
        # which the hub cache's symlinks do
        folder = cache / f"{spec['repo'].split('/')[-1]}@{spec['revision'][:8]}"
        get = lambda f: hf_hub_download(spec["repo"], f, revision=spec["revision"], local_dir=folder)
        if spec.get("data"):
            get(spec["data"])
        sess = ort.InferenceSession(get(spec["model"]), providers=["CPUExecutionProvider"])
        tok = Tokenizer.from_file(get("tokenizer.json"))
        tok.enable_truncation(spec["max_tokens"])
        tok.no_padding()
        _encoders[name] = (sess, tok)
    sess, tok = _encoders[name]
    empty = {n: np.zeros(shape, np.float32) for n, shape in spec.get("empty", {}).items()}  # text only: no image, video or audio
    out = []
    for t in texts:
        ids = np.array([tok.encode(t).ids], np.int64)
        feed = {"input_ids": ids, "attention_mask": np.ones_like(ids), **empty}
        if spec.get("token_types"):
            feed["token_type_ids"] = np.zeros_like(ids)
        v = sess.run([spec["output"]], feed)[0][0]
        if v.ndim == 2:  # a state per token: the first token's (cls), else their mean
            v = v[0] if spec.get("pool") == "cls" else v.mean(0)
        out.append(v / np.linalg.norm(v))
    return np.array(out, dtype=np.float32)


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
    memorised. On BANKING77 the plain student was underconfident (calibration error 0.118 on the holdout's raw
    gold); T=0.55 brought it to 0.049 (0.033 on reviewed gold), with the same answers.
    Held-out predictions that are almost never wrong can't say how sure to be: the fit would push T toward 0
    and make the student certain of anything, off-topic text included. So below MIN_MISSES held-out mistakes it
    stays 1, and T is kept within [0.25, 4]."""
    import numpy as np
    Z = np.zeros((len(y), k), np.float32)
    for f in set(folds.tolist()):
        tr = folds != f
        if len(set(y[tr].tolist())) < 2:  # too few rows to hold any out: leave the confidence as it is
            return 1.0
        W, b = fit(X[tr], y[tr], k)
        Z[~tr] = X[~tr] @ W + b
    if int((Z.argmax(1) != y).sum()) < MIN_MISSES:
        return 1.0

    def nll(t):
        L = Z / t
        L = L - L.max(1, keepdims=True)
        return -(L[np.arange(len(y)), y] - np.log(np.exp(L).sum(1))).mean()
    grid = np.exp(np.linspace(np.log(0.25), np.log(4), 200))
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


def distill(project: dict, node: str, encoder: str = DEFAULT) -> tuple[Path, list[str]]:
    """Train on the store's answers for `node` over its source rows (or --traffic); ask nothing. Returns the model
    folder and one report line per question."""
    require()
    import numpy as np

    from hunch.answers import attach_gold, load_reviews
    from hunch.execute import execute
    res = execute(project, dry=True)[node]  # dry: the answers already in the store, nothing asked
    spec = res["spec"]
    attach_gold(res["items"], load_reviews(spec))
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
    print(f"  encoding {len(texts)} rows with {encoder}", file=sys.stderr)  # minutes, with the larger encoder
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


def export(project: dict, node: str) -> tuple[Path, list[str]]:
    """Write the judgment's distilled model as two files a web page can load, in `<model folder>/browser/`:
    `student.json` (the encoder to fetch, how a row becomes text, each question's layer, temperature and `act`)
    and `student.js`, the module that answers with them. Nothing is trained or asked, and the model folder's own
    files are untouched. The page must repeat `answer` exactly, or its confidence is not the one `hunch test`
    measured, so a spec the module can't reproduce is refused, and the file carries a few made-up rows with the
    embedding and probabilities this machine gives them, for the module's check(). The file is served to anyone
    who loads the page: it holds no source rows and no record of what the model trained on. And the student then
    runs on the visitor's machine, where they can change what it answers: fine for a convenience, not on its own
    for a decision that protects something (the guide says so too)."""
    require()
    import base64
    import shutil

    from hunch.spec import item, state_columns, upstream
    spec = project["nodes"][node]
    model = spec["model"]
    if not model.startswith("distilled:"):
        raise SystemExit(f"{node}: its model is {model}; export writes a distilled one "
                         f"(model: distilled:<folder> in the spec, or --model distilled:<folder>)")
    for key, why in (("redact", "its rules are Python regexes"), ("chain", "its confidence depends on the judgment before it")):
        if spec.get(key):
            raise SystemExit(f"{node}: `{key}` can't be exported: {why}, which the browser module does not reproduce")
    if upstream(spec):
        raise SystemExit(f"{node}: reads from {', '.join(upstream(spec))}; export writes a judgment that reads rows itself")
    meta, w = load(spec, model)
    if meta["encoder"] not in FILES:
        raise SystemExit(f"{model}: no browser build of its encoder {meta['encoder']!r} ({', '.join(FILES)})")
    cols = state_columns(spec)
    rows = [dict.fromkeys(cols, text) for text in PROBES]
    items = [item(spec, r, qid) for r in rows for qid in spec["questions"]]
    answers = answer(spec, model, items)  # refuses a question the model was not trained on, as worded now
    b64 = lambda a: base64.b64encode(a.astype("<f4").tobytes()).decode()
    questions = {qid: {"type": m["type"], "classes": m["classes"], "legend": m["legend"],
                       "temperature": m.get("temperature", 1.0), "act": spec["questions"][qid].get("act"),
                       "W": b64(w[f"{qid}.W"]), "b": b64(w[f"{qid}.b"])}
                 for qid in spec["questions"] for m in [meta["questions"][qid]]}

    def vector(a: dict, classes: list[str]) -> list[float]:
        if a["type"] == "noul":
            return [a["noul"] if c == "yes" else round(1 - a["noul"], 4) for c in classes]
        return [a["probabilities"].get(c, 0.0) for c in classes]
    # the embedding too: made-up text gets flat probabilities, which a wrong encoder build would barely move
    embedded = encode(meta["encoder"], [text_of(items[i * len(questions)]["state"]) for i in range(len(rows))])
    probes = [{"row": r, "embedding": b64(e),
               "probabilities": {it["qid"]: vector(answers[it["key"]], questions[it["qid"]]["classes"])
                                 for it in items if it["row"] is r}} for r, e in zip(rows, embedded)]
    out = (spec["_dir"] / model.split(":", 1)[1]).resolve() / "browser"
    out.mkdir(exist_ok=True)
    student = {"format": 1, "judgment": node, "encoder": {"name": meta["encoder"], **FILES[meta["encoder"]]},
               "state": spec["state"] if isinstance(spec["state"], str) else cols,
               "clip": {c: n for c, n in (spec.get("clip") or {}).items() if c in cols},
               "questions": questions, "probes": probes}
    (out / "student.json").write_text(json.dumps(student, ensure_ascii=False) + "\n")
    shutil.copyfile(Path(__file__).with_name("student.js"), out / "student.js")
    enc = student["encoder"]
    return out, [f"  student.json: {len(questions)} question{'s' * (len(questions) != 1)}, "
                 f"{(out / 'student.json').stat().st_size // 1024} KB; student.js runs it",
                 f"  the page fetches the {enc['name']} encoder from huggingface.co/{enc['repo']}"
                 + (", and runs it on WebGPU where the browser has it" if enc.get("webgpu") else "")]


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
