# /// script
# requires-python = ">=3.12"
# dependencies = ["httpx", "pyyaml"]
# ///
"""hunch prototype: YAML judgments over a CSV, content-addressed cache, tests, backtest diff, review, online judge.

Throwaway. One file on purpose. See docs/04-design.md for what this is exploring.

  uv run hunch.py lint    SPEC
  uv run hunch.py compile SPEC
  uv run hunch.py run     SPEC
  uv run hunch.py test    SPEC
  uv run hunch.py diff    SPEC --against OLD_SPEC | git:REF
  uv run hunch.py review  SPEC [--list] [--limit N]
  (all take --source CSV to run on other rows, e.g. a holdout)

  from hunch import judge, ajudge      # online: same spec, same cache keys as batch
"""

import argparse
import asyncio
import csv
import getpass
import hashlib
import json
import math
import os
import random
import sqlite3
import subprocess
import sys
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

import httpx
import yaml

API = "https://api.typesafe.ai/v1/systemone"
PRICE_PER_INPUT_TOKEN = 0.042 / 1_000_000  # output tokens are free
HUNCH_ONLY_FIELDS = {"act", "gold"}  # routing/test config: never sent, never part of the key
QUESTION_KEYS = {"type", "instructions", "criteria"} | HUNCH_ONLY_FIELDS
SPEC_KEYS = {"judgment", "model", "source", "key", "state", "questions", "tests"}
TEST_KEYS = {"min_accuracy", "max_calibration_error", "min_act_accuracy", "min_auroc", "order_stability"}
REQUEST_OVERHEAD_TOKENS = 275  # measured on jev-1.13.0: fixed input tokens per request beyond ~chars/4
CONCURRENCY = 16
NOISE = 0.10  # measured run-to-run sd ~0.03 on ambiguous choices; flips inside this margin are flagged
DIAL = [0.5, 0.6, 0.7, 0.8, 0.9, 0.95, 0.99]
SHOW = 12  # rows listed per section; summaries always cover everything
REVIEW_FIELDS = ["qid", "row_id", "state_hash", "verdict", "label", "reviewer", "at"]


# ---------- spec ----------

def load_spec(path: Path, text: str | None = None) -> dict:
    spec = yaml.safe_load(text if text is not None else path.read_text())
    if spec["model"].endswith("latest"):
        sys.exit(f"{path}: pin an exact model version, not {spec['model']!r} (answers from different versions would share keys)")
    spec["_dir"] = path.parent
    return spec


def load_spec_ref(ref: str, current: Path) -> dict:
    """`git:REF` loads the spec as committed at REF; anything else is a file path.
    Either way it resolves against the *current* spec's dir: a backtest runs old logic on today's data."""
    if ref.startswith("git:"):
        text = subprocess.run(
            ["git", "-C", str(current.parent), "show", f"{ref[4:]}:./{current.name}"],
            check=True, capture_output=True, text=True,
        ).stdout
    else:
        text = Path(ref).read_text()
    return load_spec(current, text)


def api_question(q: dict) -> dict:
    return {k: v for k, v in q.items() if k not in HUNCH_ONLY_FIELDS}


def source_path(spec: dict) -> Path:
    return spec["_dir"] / spec["source"]


def rows(spec: dict) -> list[dict]:
    with open(source_path(spec), newline="") as f:
        return list(csv.DictReader(f))


def canon(v):
    """Line endings are transport noise: an app posting a form (\\r\\n) and a batch reading a file (\\n) must share keys.
    Applied to what is sent as well as what is hashed, so one key never stands for two different inputs."""
    return v.replace("\r\n", "\n").replace("\r", "\n") if isinstance(v, str) else v


def state_of(spec: dict, row: dict) -> dict:
    missing = [c for c in spec["state"] if c not in row]
    if missing:
        raise KeyError(f"{spec['judgment']}: state needs {missing}")
    return {col: canon(row[col]) for col in spec["state"]}


def digest(obj) -> str:
    # No sort_keys: option order is model input (it changes answers), so it must change the key.
    return hashlib.sha256(json.dumps(obj, ensure_ascii=False).encode()).hexdigest()


def item(spec: dict, row: dict, qid: str, aq: dict | None = None, variant: str = "") -> dict:
    """One (row, question) with the exact key that identifies its answer.
    `rid` is the id inside the request: variants of one question can share a request (same state, read once)."""
    q = spec["questions"][qid]
    aq = aq or api_question(q)
    state = state_of(spec, row)
    return {"row": row, "id": str(row.get(spec["key"], "online")), "qid": qid, "rid": qid + variant,
            "q": q, "aq": aq, "state": state, "shash": digest(state)[:16],
            "key": digest({"model": spec["model"], "state": state, "question": aq})}


def plan(spec: dict, rs: list[dict] | None = None) -> list[dict]:
    return [item(spec, r, qid) for r in (rows(spec) if rs is None else rs) for qid in spec["questions"]]


def label_of(spec: dict, row: dict, width: int = 60) -> str:
    text = " | ".join(row[c] for c in spec["state"]).replace("\n", " ")
    return text if len(text) <= width else text[: width - 1] + "…"


# ---------- lint ----------

def lint(spec: dict) -> tuple[list[str], list[str]]:
    """(errors, warnings). Rules come from API limits and from what the prototype measured."""
    errors, warnings = [], []
    for k in set(spec) - SPEC_KEYS - {"_dir"}:
        warnings.append(f"unknown spec key {k!r} (typo?)")
    try:
        with open(source_path(spec), newline="") as f:
            header = next(csv.reader(f))
    except FileNotFoundError:
        errors.append(f"source not found: {source_path(spec)}")
        header = None
    if header is not None:
        for col in [spec["key"], *spec["state"]]:
            if col not in header:
                errors.append(f"column {col!r} not in {spec['source']} (has {header})")
    for qid, q in spec["questions"].items():
        for k in set(q) - QUESTION_KEYS:
            warnings.append(f"{qid}: unknown key {k!r} (typo?)")
        if "act" in q and not 0 < q["act"] <= 1:
            errors.append(f"{qid}: act must be in (0, 1], got {q['act']}")
        if header is not None and q.get("gold") and q["gold"] not in header:
            # normal for production rows (no gold yet); a warning still catches a typo in the column name
            warnings.append(f"{qid}: gold column {q['gold']!r} not in {Path(spec['source']).name}; these rows have no gold")
        if q["type"] == "choice":
            crit = q.get("criteria") or {}
            if len(crit) > 255:
                errors.append(f"{qid}: {len(crit)} options; the API accepts at most 255")
            bare = [o for o, d in crit.items() if d in (None, "")]
            if 0 < len(bare) < len(crit):
                warnings.append(
                    f"{qid}: {len(crit) - len(bare)} of {len(crit)} options described, {len(bare)} bare "
                    f"(e.g. {', '.join(bare[:4])}). Describe all or none: described options pull answers "
                    f"away from bare neighbours (BANKING77: partial 22 fixed/14 broken, n.s.; full 28/5, p<0.001)")
        if q["type"] == "score" and not 2 <= len(q.get("criteria") or []) <= 10:
            errors.append(f"{qid}: score needs 2 to 10 levels")
    for qid, conf in (spec.get("tests") or {}).items():
        if qid not in spec["questions"]:
            errors.append(f"tests: no question {qid!r}")
            continue
        for k in set(conf) - TEST_KEYS:
            warnings.append(f"tests.{qid}: unknown test {k!r} (typo?)")
        if "order_stability" in conf and spec["questions"][qid]["type"] != "choice":
            warnings.append(f"tests.{qid}: order_stability only applies to choice questions")
    return errors, warnings


# ---------- store ----------

_conns: dict[Path, sqlite3.Connection] = {}


def open_store(spec: dict) -> sqlite3.Connection:
    """SQLite in WAL mode: many processes (batch runs, apps calling judge()) can share it.
    One connection per process and store; writes are short explicit transactions."""
    path = (spec["_dir"] / ".hunch" / "store.sqlite").resolve()
    if path in _conns:
        return _conns[path]
    path.parent.mkdir(exist_ok=True)
    db = sqlite3.connect(path, timeout=30, isolation_level=None, check_same_thread=False)
    for _ in range(100):  # switching to WAL needs a moment alone with the file; it persists once set
        try:
            if db.execute("pragma journal_mode").fetchone()[0] != "wal":
                db.execute("pragma journal_mode=wal")
            break
        except sqlite3.OperationalError:
            time.sleep(0.05)
    db.execute("pragma synchronous=normal")
    db.execute("""create table if not exists answers (
        key text primary key, model text, answer text, input_tokens real,
        created_at text default current_timestamp)""")
    _conns[path] = db
    return db


def write(db: sqlite3.Connection, sql: str, rows: list) -> None:
    db.execute("begin immediate")
    try:
        db.executemany(sql, rows)
        db.execute("commit")
    except BaseException:
        db.execute("rollback")
        raise


def cached(db, keys: list[str]) -> dict[str, dict]:
    out = {}
    for i in range(0, len(keys), 500):
        chunk = keys[i:i + 500]
        q = f"select key, answer from answers where key in ({','.join('?' * len(chunk))})"
        out |= {k: json.loads(a) for k, a in db.execute(q, chunk)}
    return out


# ---------- engine ----------

async def ask(client: httpx.AsyncClient, sem, model: str, state: dict, questions: dict) -> dict:
    body = {"model": model, "state": state, "questions": questions}
    last = "no response"
    async with sem:
        for attempt in range(8):
            try:
                r = await client.post(API, json=body)
            except httpx.TransportError as e:
                last = repr(e)
                await asyncio.sleep(0.5 * 2**attempt)
                continue
            if r.status_code in (429, 529) or r.status_code >= 500:
                last = f"HTTP {r.status_code}"
                await asyncio.sleep(min(float(r.headers.get("retry-after") or 0.5 * 2**attempt), 30))
                continue
            if r.status_code >= 400:
                raise RuntimeError(f"HTTP {r.status_code}: {r.text[:300]}")
            return r.json()
    raise RuntimeError(f"gave up after retries ({last})")


async def fill(spec: dict, db, items: list[dict]) -> dict:
    """Ensure every item has an answer. Missing questions of the same row share one request (read once).
    Each response is saved as it arrives, so a failure part-way loses nothing already paid for."""
    have = cached(db, list({it["key"] for it in items}))
    missing: dict[str, dict[str, dict]] = {}
    for it in items:
        if it["key"] not in have:
            missing.setdefault(it["id"], {})[it["key"]] = it  # dedupe identical keys within a row
    group = [list(g.values()) for g in missing.values()]
    stats = {"cached": sum(it["key"] in have for it in items), "asked": 0, "requests": len(group), "tokens": 0}

    if group:
        key = os.environ.get("TYPESAFE_API_KEY") or os.environ["TYPESAFE_AI_API_KEY"]
        async with httpx.AsyncClient(headers={"Authorization": f"Bearer {key}"}, timeout=120) as client:
            sem = asyncio.Semaphore(CONCURRENCY)

            async def one(g: list[dict]) -> None:
                res = await ask(client, sem, spec["model"], g[0]["state"], {it["rid"]: it["aq"] for it in g})
                tokens = res["usage"]["input_tokens"]
                write(db, "insert or replace into answers (key, model, answer, input_tokens) values (?, ?, ?, ?)",
                      [[it["key"], res["model"], json.dumps(res["answers"][it["rid"]]), tokens / len(g)] for it in g])
                stats["tokens"] += tokens
                stats["asked"] += len(g)
                for it in g:
                    have[it["key"]] = res["answers"][it["rid"]]

            results = await asyncio.gather(*[one(g) for g in group], return_exceptions=True)
        failed = [r for r in results if isinstance(r, BaseException)]
        if failed:
            raise RuntimeError(f"{len(failed)}/{len(group)} requests failed; {stats['asked']} answers saved, "
                               f"re-run to retry only the rest. First error: {failed[0]}") from failed[0]
    stats["cost"] = stats["tokens"] * PRICE_PER_INPUT_TOKEN
    return have | {"_stats": stats}


def merge_stats(*answer_sets: dict) -> dict:
    return {k: sum(a["_stats"][k] for a in answer_sets) for k in ("cached", "asked", "requests", "tokens", "cost")}


# ---------- interpretation ----------

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


def route(q: dict, confidence: float) -> str:
    return "" if "act" not in q else ("act" if confidence >= q["act"] else "review")


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
    """Chance a random positive scores above a random negative (ties count half)."""
    wins = sum((p > n) + 0.5 * (p == n) for p in pos for n in neg)
    return wins / (len(pos) * len(neg))


def calibration(pairs: list[tuple[float, bool]], bins: int = 10) -> tuple[float, list[tuple]]:
    """Expected calibration error over equal-width bins, and the reliability table."""
    table, ece = [], 0.0
    for b in range(bins):
        lo, hi = b / bins, (b + 1) / bins
        sel = [(p, h) for p, h in pairs if lo <= p < hi or (b == bins - 1 and p == hi)]
        if sel:
            conf, acc = sum(p for p, _ in sel) / len(sel), sum(h for _, h in sel) / len(sel)
            ece += len(sel) / len(pairs) * abs(acc - conf)
            table.append((lo, hi, len(sel), conf, acc))
    return ece, table


# ---------- gold and reviews ----------

def reviews_path(spec: dict) -> Path:
    return spec["_dir"] / f"{spec['judgment']}.reviews.csv"


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


def normalize_gold(q: dict, v: str) -> str | None:
    v = (v or "").strip()
    if not v:
        return None
    return ("yes" if is_yes(v) else "no") if q["type"] == "noul" else v


def attach_gold(items: list[dict], reviews: dict) -> None:
    """Effective gold = a human verdict on this exact row text if there is one, else the source column.
    `ambiguous` verdicts drop the row from scoring. A verdict on text that has since changed is ignored."""
    for it in items:
        col = it["q"].get("gold")
        it["raw_gold"] = normalize_gold(it["q"], it["row"].get(col, "")) if col else None
        r = reviews.get((it["qid"], it["id"]))
        if r and r["state_hash"] == it["shash"]:
            ambiguous = r["verdict"] == "ambiguous"
            it["gold"], it["gold_src"] = (None, "excluded") if ambiguous else (r["label"], "review")
        else:
            it["gold"], it["gold_src"] = it["raw_gold"], ("source" if it["raw_gold"] else None)


# ---------- order stability ----------

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


# ---------- commands ----------

def load(spec: dict) -> tuple[sqlite3.Connection, list[dict], dict]:
    db = open_store(spec)
    items = plan(spec)
    answers = asyncio.run(fill(spec, db, items))
    attach_gold(items, load_reviews(spec))
    return db, items, answers


def cmd_lint(spec: dict, _args) -> None:
    print("lint: ok")  # main() already printed any findings and exits on errors


def cmd_compile(spec: dict, _args) -> None:
    items = plan(spec)
    first = items[0]
    payload = {"model": spec["model"], "state": first["state"],
               "questions": {it["qid"]: it["aq"] for it in items if it["id"] == first["id"]}}
    print(f"# request for row {first['id']} (one request per row, all questions read once)")
    print(json.dumps(payload, indent=2, ensure_ascii=False))
    have = cached(open_store(spec), [it["key"] for it in items])
    todo = [it for it in items if it["key"] not in have]
    states = {it["id"]: it["state"] for it in todo}  # state is read once per request
    est = (sum(len(json.dumps(s)) for s in states.values()) + sum(len(json.dumps(it["aq"])) for it in todo)) / 4 \
        + REQUEST_OVERHEAD_TOKENS * len(states)
    print(f"\n# {len(items)} answers planned, {len(items) - len(todo)} cached, {len(todo)} to ask, "
          f"~{est:,.0f} input tokens, ~${est * PRICE_PER_INPUT_TOKEN:.5f}")


def materialize(spec: dict, db, items: list[dict], answers: dict) -> None:
    by_row: dict[str, dict] = {}
    for it in items:
        a = answers[it["key"]]
        label, conf, _ = decide(a)
        r = by_row.setdefault(it["id"], {spec["key"]: it["id"]})
        r[it["qid"]], r[f"{it['qid']}_p"] = label, round(conf, 3)
        if a["type"] == "noul":
            r[f"{it['qid']}_pyes"] = round(a["noul"], 3)
        if "act" in it["q"]:
            r[f"{it['qid']}_route"] = route(it["q"], conf)
    recs = list(by_row.values())
    cols = list(recs[0])
    typed = ", ".join(f'"{c}" {"real" if c.endswith(("_p", "_pyes")) else "text"}' for c in cols)
    table = spec["judgment"]
    db.execute("begin immediate")
    try:
        db.execute(f'drop table if exists "{table}"')
        db.execute(f'create table "{table}" ({typed})')
        db.executemany(f'insert into "{table}" values ({", ".join("?" * len(cols))})', [[r[c] for c in cols] for r in recs])
        db.execute("commit")
    except BaseException:
        db.execute("rollback")
        raise


def print_stats(s: dict) -> None:
    print(f"answers: {s['cached']} cached, {s['asked']} asked in {s['requests']} requests, "
          f"{s['tokens']:,} input tokens, ${s['cost']:.5f}")


def cmd_run(spec: dict, _args) -> None:
    db, items, answers = load(spec)
    print_stats(answers["_stats"])
    materialize(spec, db, items, answers)
    print(f"materialized table \"{spec['judgment']}\" in .hunch/store.sqlite ({len({it['id'] for it in items})} rows)")
    for qid, q in spec["questions"].items():
        if "act" in q:
            n = sum(1 for it in items if it["qid"] == qid and route(q, decide(answers[it["key"]])[1]) == "review")
            print(f"  {qid}: {n} rows below act={q['act']} → review queue")


def accuracy(items: list[dict], answers: dict, qid: str, gold: str = "gold") -> float | None:
    its = [it for it in items if it["qid"] == qid and it[gold]]
    return sum(decide(answers[it["key"]])[0] == it[gold] for it in its) / len(its) if its else None


def calib_pairs(its: list[dict], answers: dict, gold: str = "gold") -> list[tuple[float, bool]]:
    """(stated probability, did it happen). choice: top p vs correct; noul: p(yes) vs gold yes."""
    out = []
    for it in its:
        a, g = answers[it["key"]], it[gold]
        if not g:
            continue
        if a["type"] == "choice":
            label, p, _ = decide(a)
            out.append((p, label == g))
        elif a["type"] == "noul":
            out.append((a["noul"], g == "yes"))
    return out


class Checks:
    """Collects PASS/FAIL lines so `test` can exit non-zero."""
    failed = False

    def __call__(self, ok: bool, text: str) -> None:
        self.failed |= not ok
        print(f"  {'PASS' if ok else 'FAIL'} {text}")


def cmd_test(spec: dict, _args) -> None:
    db, items, answers = load(spec)
    check = Checks()
    all_answers = [answers]

    for qid, q in spec["questions"].items():
        conf = (spec.get("tests") or {}).get(qid, {})
        its = [it for it in items if it["qid"] == qid]
        print(f"\n{qid} ({q['type']}, {len(its)} rows)")
        src = Counter(it["gold_src"] for it in its)
        gold_its = [it for it in its if it["gold"]]
        if not gold_its:
            continue
        print(f"  gold: {len(gold_its)} rows ({src['source']} from source, {src['review']} from review"
              f"{f', {src['excluded']} excluded as ambiguous' if src['excluded'] else ''})")
        reviewed = src["review"] + src["excluded"] > 0

        acc = accuracy(items, answers, qid)
        raw = f" (raw source gold: {accuracy(items, answers, qid, 'raw_gold'):.1%})" if reviewed else ""
        check(acc >= conf.get("min_accuracy", 0), f"accuracy {acc:.1%}{raw} (min {conf.get('min_accuracy', 0):.0%})")

        pairs = calib_pairs(its, answers)
        ece, table = calibration(pairs)
        raw = f" (raw source gold: {calibration(calib_pairs(its, answers, 'raw_gold'))[0]:.3f})" if reviewed else ""
        check(ece <= conf.get("max_calibration_error", 1), f"calibration error {ece:.3f}{raw} (max {conf.get('max_calibration_error', 1)})")
        print(f"         {'stated p(yes)' if q['type'] == 'noul' else 'stated p':<13} {'n':>5}   avg stated   observed")
        for lo, hi, n, c, a in table:
            print(f"       {lo:.1f}–{hi:.1f}      {n:>6}   {c:>10.3f}   {a:>8.3f}")

        if q["type"] == "noul":
            pos = [answers[it["key"]]["noul"] for it in gold_its if it["gold"] == "yes"]
            neg = [answers[it["key"]]["noul"] for it in gold_its if it["gold"] == "no"]
            if pos and neg:
                auc = auroc(pos, neg)
                check(auc >= conf.get("min_auroc", 0),
                      f"AUROC {auc:.3f} ({len(pos)} yes / {len(neg)} no; 0.5 = coin toss) (min {conf.get('min_auroc', 0)})")

        dial = [(decide(answers[it["key"]])[1], decide(answers[it["key"]])[0] == it["gold"]) for it in gold_its]
        print("       dial   automated   wrong among automated")
        for t in DIAL:
            auto = [h for p, h in dial if p >= t]
            wrong = (1 - sum(auto) / len(auto)) if auto else 0
            print(f"       {t:>4.2f}   {len(auto) / len(dial):>9.1%}   {wrong:>21.1%}{'  ← act' if t == q.get('act') else ''}")
        if "act" in q and "min_act_accuracy" in conf:
            auto = [h for p, h in dial if p >= q["act"]]
            a = sum(auto) / len(auto) if auto else 1
            check(a >= conf["min_act_accuracy"],
                  f"accuracy among auto-acted {a:.1%} on {len(auto) / len(dial):.0%} of rows at act={q['act']} (min {conf['min_act_accuracy']:.0%})")

        wrong = sorted((it for it in gold_its if decide(answers[it["key"]])[0] != it["gold"]),
                       key=lambda it: -decide(answers[it["key"]])[1])
        print(f"       most confident mistakes ({len(wrong)} total; high confidence + wrong = dangerous, or a gold error → hunch review):")
        for it in wrong[:SHOW]:
            print(f"         #{it['id']:>4} gold={it['gold']:<32} got {show(answers[it['key']]):<38} {label_of(spec, it['row'], 50)}")
        print("       most confused (gold → got):")
        for (g, got), n in Counter((it["gold"], decide(answers[it["key"]])[0]) for it in wrong).most_common(8):
            print(f"         {n:>3}  {g} → {got}")

        order = conf.get("order_stability")
        if order and q["type"] == "choice":
            base = sample(its, order.get("sample", 100))
            variants = [item(spec, it["row"], qid, permuted(it["aq"], k), f"~p{k}")
                        for k in range(1, order.get("permutations", 2) + 1) for it in base]
            vans = asyncio.run(fill(spec, db, variants))
            all_answers.append(vans)
            by_id = {it["id"]: it for it in base}
            flips, noisy, dp = [], 0, []
            for v in variants:
                b = by_id[v["id"]]
                (bl, bp, bm), (vl, _, vm) = decide(answers[b["key"]]), decide(vans[v["key"]])
                dp.append(abs(bp - vans[v["key"]]["probabilities"][bl]))
                if bl != vl:
                    flips.append((b, v))
                    noisy += min(bm, vm) < NOISE
            rate = len(flips) / len(variants)
            check(rate <= order.get("max_flip_rate", 1),
                  f"order stability: {len(flips)}/{len(variants)} answers flip ({rate:.1%}, {noisy} within noise band), "
                  f"mean |Δp| of original answer {sum(dp) / len(dp):.3f} (max flip rate {order.get('max_flip_rate', 1):.0%})")
            for b, v in flips[:SHOW]:
                print(f"         #{b['id']:>4} {v['rid']:<14} {show(answers[b['key']]):<36} → {show(vans[v['key']]):<36} {label_of(spec, b['row'], 40)}")

    print()
    print_stats(merge_stats(*all_answers))
    sys.exit(1 if check.failed else 0)


def cmd_diff(spec: dict, args) -> None:
    old = load_spec_ref(args.against, args.spec)
    old["source"] = spec["source"]  # a backtest compares logic, never data: both sides read the same rows
    db = open_store(spec)
    new_items, old_items = plan(spec), plan(old)
    new_a = asyncio.run(fill(spec, db, new_items))
    old_a = asyncio.run(fill(old, db, old_items))
    reviews = load_reviews(spec)  # gold is about the data, so both sides use today's reviews
    attach_gold(new_items, reviews)
    attach_gold(old_items, reviews)
    print_stats(merge_stats(new_a, old_a))

    old_by = {(it["id"], it["qid"]): it for it in old_items}
    for qid, q in spec["questions"].items():
        pairs = [(old_by.get((it["id"], qid)), it) for it in new_items if it["qid"] == qid]
        if not any(o for o, _ in pairs):
            print(f"\n{qid}: new question")
            continue
        if all(o and o["key"] == n["key"] for o, n in pairs):
            print(f"\n{qid}: unchanged (same keys, 0 calls)")
            continue
        flips = []
        for o, n in pairs:
            oa, na = old_a[o["key"]], new_a[n["key"]]
            (ol, _, om), (nl, _, nm) = decide(oa), decide(na)
            if ol != nl:
                flips.append((n, oa, na, min(om, nm) < NOISE))
        print(f"\n{qid}: {len(flips)}/{len(pairs)} rows flip ({sum(f[3] for f in flips)} within noise band)")
        if any(n["gold"] for _, n in pairs):
            fixed = sum(decide(na)[0] == n["gold"] for n, _, na, _ in flips if n["gold"])
            broke = sum(decide(oa)[0] == n["gold"] for n, oa, _, _ in flips if n["gold"])
            p = sign_test(fixed, broke)
            verdict = "significant" if p < 0.05 else "NOT significant: could be noise, get more gold rows"
            print(f"  gold accuracy {accuracy(old_items, old_a, qid):.1%} → {accuracy(new_items, new_a, qid):.1%}"
                  f"  (✓ {fixed} fixed, ✗ {broke} broken, {len(flips) - fixed - broke} other)"
                  f"\n  paired sign test p={p:.3f} → {verdict}")
        flips.sort(key=lambda f: f[3])  # real flips first, noise-band flips last
        for n, oa, na, noisy in flips[:SHOW]:
            g = n["gold"]
            mark = "✓" if g and decide(na)[0] == g else ("✗" if g and decide(oa)[0] == g else " ")
            print(f"  {mark} #{n['id']:>4} {show(oa):>36} → {show(na):<36}{' ~noise' if noisy else '       '} {label_of(spec, n['row'], 40)}")
        if len(flips) > SHOW:
            print(f"  … {len(flips) - SHOW} more")


def review_queue(items: list[dict], answers: dict) -> list[tuple[str, dict]]:
    """disputed: confident answer disagrees with source gold (a model error, or a gold error).
    uncertain: below the act threshold and no gold yet (a human label makes it gold).
    Rows with a current human verdict are done."""
    disputed, uncertain = [], []
    for it in items:
        if "act" not in it["q"] or it["gold_src"] in ("review", "excluded"):
            continue
        label, conf, _ = decide(answers[it["key"]])
        if it["raw_gold"] and label != it["raw_gold"] and conf >= it["q"]["act"]:
            disputed.append(it)
        elif not it["raw_gold"] and conf < it["q"]["act"]:
            uncertain.append(it)
    disputed.sort(key=lambda it: -decide(answers[it["key"]])[1])
    uncertain.sort(key=lambda it: decide(answers[it["key"]])[1])
    return [("disputed", it) for it in disputed] + [("uncertain", it) for it in uncertain]


def cmd_review(spec: dict, args) -> None:
    _, items, answers = load(spec)
    queue = review_queue(items, answers)
    kinds = Counter(k for k, _ in queue)
    print(f"review queue: {kinds['disputed']} disputed (confident answer ≠ gold), "
          f"{kinds['uncertain']} uncertain (below act, no gold) → verdicts go to {reviews_path(spec).name}")
    queue = queue[: args.limit] if args.limit else queue
    if args.list:
        for kind, it in queue:
            print(f"  {kind:<9} #{it['id']:>5} {it['qid']:<10} {show(answers[it['key']]):<36} "
                  f"gold={it['raw_gold'] or '-':<24} {label_of(spec, it['row'], 50)}")
        return

    reviewer = args.reviewer or getpass.getuser()
    done = 0
    for n, (kind, it) in enumerate(queue, 1):
        a = answers[it["key"]]
        top = ranked(a)[:3]
        options = set(it["aq"].get("criteria") or {}) if a["type"] == "choice" else {"yes", "no"}
        print(f"\n[{kind} {n}/{len(queue)}] #{it['id']}  {it['qid']}")
        for col in spec["state"]:
            print(f"  {col}: {label_of({'state': [col]}, it['row'], 400)}")
        if kind == "disputed":
            print(f"  answer key: {it['raw_gold']}")
        print("  model:      " + "   ".join(f"{i}) {lab} {p:.2f}" for i, (lab, p) in enumerate(top, 1)))
        keys = "[m] model is right   [k] answer key is right   " if kind == "disputed" else ""
        print(f"  {keys}[1-3] pick   [a] ambiguous   [s] skip   [q] quit   or type an option")
        while True:
            try:
                ans = input("> ").strip()
            except EOFError:
                ans = "q"
            verdict = label = None
            if ans == "q":
                print(f"\n{done} verdicts saved to {reviews_path(spec).name}")
                return
            if ans == "s":
                break
            if ans == "a":
                verdict = "ambiguous"
            elif ans == "m" and kind == "disputed":
                verdict, label = "model_right", top[0][0]
            elif ans == "k" and kind == "disputed":
                verdict, label = "key_right", it["raw_gold"]
            elif ans in {"1", "2", "3"} and int(ans) <= len(top):
                verdict, label = "labeled", top[int(ans) - 1][0]
            elif ans in options:
                verdict, label = "labeled", ans
            else:
                print("  ? not an option")
                continue
            append_review(spec, {"qid": it["qid"], "row_id": it["id"], "state_hash": it["shash"],
                                 "verdict": verdict, "label": label or "", "reviewer": reviewer,
                                 "at": datetime.now(timezone.utc).isoformat(timespec="seconds")})
            done += 1
            break
    print(f"\n{done} verdicts saved to {reviews_path(spec).name}")


# ---------- online ----------

_specs: dict[Path, dict] = {}


async def ajudge(spec_path: str | Path, **fields) -> dict:
    """Judge one row inside an app. Same spec, same keys as batch: a row the batch already judged is a cache hit,
    and a row judged online is a cache hit for the next batch run."""
    path = Path(spec_path).resolve()
    spec = _specs.get(path) or _specs.setdefault(path, load_spec(path))
    items = plan(spec, [fields])
    db = open_store(spec)
    hits = cached(db, [it["key"] for it in items]).keys()
    answers = await fill(spec, db, items)
    out = {}
    for it in items:
        label, conf, _ = decide(answers[it["key"]])
        out[it["qid"]] = {"label": label, "p": conf, "route": route(it["q"], conf), "cached": it["key"] in hits}
    return out


def judge(spec_path: str | Path, **fields) -> dict:
    return asyncio.run(ajudge(spec_path, **fields))


def main() -> None:
    commands = {"lint": cmd_lint, "compile": cmd_compile, "run": cmd_run, "test": cmd_test,
                "diff": cmd_diff, "review": cmd_review}
    p = argparse.ArgumentParser(prog="hunch")
    p.add_argument("command", choices=list(commands))
    p.add_argument("spec", type=Path)
    p.add_argument("--against", help="diff: old spec path, or git:REF")
    p.add_argument("--source", type=Path, help="run on this CSV instead of the spec's source (e.g. a holdout set)")
    p.add_argument("--list", action="store_true", help="review: print the queue without prompting")
    p.add_argument("--limit", type=int, help="review: at most N items")
    p.add_argument("--reviewer", help="review: name recorded with each verdict (default: $USER)")
    args = p.parse_args()
    spec = load_spec(args.spec)
    if args.source:
        spec["source"] = args.source.resolve()
    errors, warnings = lint(spec)
    for w in warnings:
        print(f"lint warning: {w}", file=sys.stderr)
    for e in errors:
        print(f"lint error: {e}", file=sys.stderr)
    if errors:
        sys.exit(2)
    commands[args.command](spec, args)


if __name__ == "__main__":
    main()
