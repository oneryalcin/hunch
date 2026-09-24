# /// script
# requires-python = ">=3.12"
# dependencies = ["httpx", "duckdb", "pyyaml"]
# ///
"""hunch prototype: YAML judgments over a CSV, content-addressed cache, tests, backtest diff, online judge.

Throwaway. One file on purpose. See docs/04-design.md for what this is exploring.

  uv run hunch.py compile SPEC
  uv run hunch.py run     SPEC
  uv run hunch.py test    SPEC
  uv run hunch.py diff    SPEC --against OLD_SPEC | git:REF

  from hunch import judge, ajudge      # online: same spec, same cache keys as batch
"""

import argparse
import asyncio
import csv
import hashlib
import json
import math
import os
import random
import subprocess
import sys
from collections import Counter
from pathlib import Path

import duckdb
import httpx
import yaml

API = "https://api.typesafe.ai/v1/systemone"
PRICE_PER_INPUT_TOKEN = 0.042 / 1_000_000  # output tokens are free
HUNCH_ONLY_FIELDS = {"act", "gold"}  # routing/test config: never sent, never part of the key
REQUEST_OVERHEAD_TOKENS = 275  # measured on jev-1.13.0: fixed input tokens per request beyond ~chars/4
CONCURRENCY = 16
NOISE = 0.10  # measured run-to-run sd ~0.03 on ambiguous choices; flips inside this margin are flagged
DIAL = [0.5, 0.6, 0.7, 0.8, 0.9, 0.95, 0.99]
SHOW = 12  # rows listed per section; summaries always cover everything


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


def rows(spec: dict) -> list[dict]:
    with open(spec["_dir"] / spec["source"], newline="") as f:
        return list(csv.DictReader(f))


def state_of(spec: dict, row: dict) -> dict:
    missing = [c for c in spec["state"] if c not in row]
    if missing:
        raise KeyError(f"{spec['judgment']}: state needs {missing}")
    return {col: row[col] for col in spec["state"]}


def cache_key(model: str, state: dict, question: dict) -> str:
    # No sort_keys: option order is model input (it changes answers), so it must change the key.
    blob = json.dumps({"model": model, "state": state, "question": question}, ensure_ascii=False)
    return hashlib.sha256(blob.encode()).hexdigest()


def item(spec: dict, row: dict, qid: str, aq: dict | None = None, variant: str = "") -> dict:
    """One (row, question) with the exact key that identifies its answer.
    `rid` is the id inside the request: variants of one question can share a request (same state, read once)."""
    q = spec["questions"][qid]
    aq = aq or api_question(q)
    state = state_of(spec, row)
    return {"row": row, "id": row.get(spec["key"], "online"), "qid": qid, "rid": qid + variant,
            "q": q, "aq": aq, "state": state, "key": cache_key(spec["model"], state, aq)}


def plan(spec: dict, rs: list[dict] | None = None) -> list[dict]:
    return [item(spec, r, qid) for r in (rows(spec) if rs is None else rs) for qid in spec["questions"]]


def label_of(spec: dict, row: dict, width: int = 60) -> str:
    text = " | ".join(row[c] for c in spec["state"])
    return text if len(text) <= width else text[: width - 1] + "…"


# ---------- store ----------

def open_store(spec: dict) -> duckdb.DuckDBPyConnection:
    d = spec["_dir"] / ".hunch"
    d.mkdir(exist_ok=True)
    db = duckdb.connect(str(d / "store.duckdb"))
    db.execute("""create table if not exists answers (
        key text primary key, model text, answer json, input_tokens double,
        created_at timestamp default current_timestamp)""")
    return db


def cached(db, keys: list[str]) -> dict[str, dict]:
    if not keys:
        return {}
    got = db.execute("select key, answer from answers where key in (select unnest(?))", [keys]).fetchall()
    return {k: json.loads(a) for k, a in got}


# ---------- engine ----------

async def ask(client: httpx.AsyncClient, sem, model: str, state: dict, questions: dict) -> dict:
    body = {"model": model, "state": state, "questions": questions}
    async with sem:
        for attempt in range(6):
            r = await client.post(API, json=body)
            if r.status_code in (429, 529) or r.status_code >= 500:
                await asyncio.sleep(0.5 * 2**attempt)
                continue
            r.raise_for_status()
            return r.json()
    r.raise_for_status()


async def fill(spec: dict, db, items: list[dict]) -> dict:
    """Ensure every item has an answer. Missing questions of the same row share one request (read once)."""
    have = cached(db, list({it["key"] for it in items}))
    missing: dict[str, dict[str, dict]] = {}
    for it in items:
        if it["key"] not in have:
            missing.setdefault(it["id"], {})[it["key"]] = it  # dedupe identical keys within a row
    group = [list(g.values()) for g in missing.values()]

    stats = {"cached": sum(it["key"] in have for it in items), "asked": sum(map(len, group)),
             "requests": len(group), "tokens": 0}
    if group:
        key = os.environ.get("TYPESAFE_API_KEY") or os.environ["TYPESAFE_AI_API_KEY"]
        async with httpx.AsyncClient(headers={"Authorization": f"Bearer {key}"}, timeout=60) as client:
            sem = asyncio.Semaphore(CONCURRENCY)
            results = await asyncio.gather(*[
                ask(client, sem, spec["model"], g[0]["state"], {it["rid"]: it["aq"] for it in g}) for g in group])
        for g, res in zip(group, results):
            tokens = res["usage"]["input_tokens"]
            stats["tokens"] += tokens
            db.executemany("insert or replace into answers (key, model, answer, input_tokens) values (?, ?, ?, ?)",
                           [[it["key"], res["model"], json.dumps(res["answers"][it["rid"]]), tokens / len(g)] for it in g])
            for it in g:
                have[it["key"]] = res["answers"][it["rid"]]
    stats["cost"] = stats["tokens"] * PRICE_PER_INPUT_TOKEN
    return have | {"_stats": stats}


def merge_stats(*answer_sets: dict) -> dict:
    return {k: sum(a["_stats"][k] for a in answer_sets) for k in ("cached", "asked", "requests", "tokens", "cost")}


# ---------- interpretation ----------

def decide(a: dict) -> tuple[str, float, float]:
    """(label, strength, margin). margin = distance from the decision boundary, used for noise flags."""
    if a["type"] == "noul":
        p = a["noul"]
        return ("yes" if p >= 0.5 else "no"), p, abs(p - 0.5) * 2
    if a["type"] == "choice":
        ps = sorted(a["probabilities"].values(), reverse=True)
        return a["choice"], ps[0], ps[0] - (ps[1] if len(ps) > 1 else 0)
    s = a["score"]  # score: nearest level
    level = str(round(s))
    return f"{level}:{a['legend'][level]}", a["confidence"], 1 - abs(s - round(s)) * 2


def route(q: dict, strength: float) -> str:
    return "" if "act" not in q else ("act" if strength >= q["act"] else "review")


def show(a: dict) -> str:
    label, strength, _ = decide(a)
    return f"{label} {strength:.2f}"


def is_yes(v: str) -> bool:
    return v.strip().lower() in {"1", "true", "yes", "y"}


def outcome(it: dict, a: dict) -> tuple[float, bool] | None:
    """(stated probability, did it happen) for calibration. choice: top p vs correct; noul: p vs gold yes."""
    col = it["q"].get("gold")
    if not col:
        return None
    if a["type"] == "choice":
        label, p, _ = decide(a)
        return p, label == it["row"][col]
    if a["type"] == "noul":
        return a["noul"], is_yes(it["row"][col])
    return None  # score calibration is open (ordinal); see docs/04-design.md


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


def sign_test(fixed: int, broke: int) -> float:
    """Two-sided exact sign test on paired flips: is 'fixed vs broken' distinguishable from a coin toss?"""
    n, k = fixed + broke, min(fixed, broke)
    return 1.0 if n == 0 else min(1.0, 2 * sum(math.comb(n, i) for i in range(k + 1)) / 2**n)


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
    return sorted(its, key=lambda it: hashlib.sha256(str(it["id"]).encode()).hexdigest())[:n]


# ---------- commands ----------

def cmd_compile(spec: dict, _args) -> None:
    items = plan(spec)
    first = items[0]
    payload = {"model": spec["model"], "state": first["state"],
               "questions": {it["qid"]: it["aq"] for it in items if it["id"] == first["id"]}}
    print(f"# request for row {first['id']} (one request per row, all questions read once)")
    print(json.dumps(payload, indent=2, ensure_ascii=False))
    db = open_store(spec)
    have = cached(db, [it["key"] for it in items])
    todo = [it for it in items if it["key"] not in have]
    states = {it["id"]: it["state"] for it in todo}  # state is read once per request
    est = (sum(len(json.dumps(s)) for s in states.values()) + sum(len(json.dumps(it["aq"])) for it in todo)) / 4 \
        + REQUEST_OVERHEAD_TOKENS * len(states)
    print(f"\n# {len(items)} answers planned, {len(items) - len(todo)} cached, {len(todo)} to ask, "
          f"~{est:,.0f} input tokens, ~${est * PRICE_PER_INPUT_TOKEN:.5f}")


def materialize(spec: dict, db, items: list[dict], answers: dict) -> None:
    by_row: dict[str, dict] = {}
    for it in items:
        label, strength, _ = decide(answers[it["key"]])
        r = by_row.setdefault(it["id"], {spec["key"]: it["id"]})
        r[it["qid"]], r[f"{it['qid']}_p"] = label, round(strength, 3)
        if "act" in it["q"]:
            r[f"{it['qid']}_route"] = route(it["q"], strength)
    table = spec["judgment"]
    recs = list(by_row.values())
    cols = list(recs[0])
    typed = ", ".join(f'"{c}" {"double" if c.endswith("_p") else "text"}' for c in cols)
    db.execute(f'create or replace table "{table}" ({typed})')
    db.executemany(f'insert into "{table}" values ({", ".join("?" * len(cols))})', [[r[c] for c in cols] for r in recs])


def print_stats(s: dict) -> None:
    print(f"answers: {s['cached']} cached, {s['asked']} asked in {s['requests']} requests, "
          f"{s['tokens']:,} input tokens, ${s['cost']:.5f}")


def cmd_run(spec: dict, _args) -> None:
    db = open_store(spec)
    items = plan(spec)
    answers = asyncio.run(fill(spec, db, items))
    print_stats(answers["_stats"])
    materialize(spec, db, items, answers)
    print(f"materialized table \"{spec['judgment']}\" in .hunch/store.duckdb ({len({it['id'] for it in items})} rows)")
    for qid, q in spec["questions"].items():
        if "act" in q:
            n = sum(1 for it in items if it["qid"] == qid and route(q, decide(answers[it["key"]])[1]) == "review")
            print(f"  {qid}: {n} rows below act={q['act']} → review queue")


def accuracy(items: list[dict], answers: dict, qid: str) -> float | None:
    its = [it for it in items if it["qid"] == qid]
    col = its[0]["q"].get("gold") if its else None
    if not col:
        return None
    return sum(decide(answers[it["key"]])[0] == it["row"][col] for it in its) / len(its)


class Checks:
    """Collects PASS/FAIL lines so `test` can exit non-zero."""
    failed = False

    def __call__(self, ok: bool, text: str) -> None:
        self.failed |= not ok
        print(f"  {'PASS' if ok else 'FAIL'} {text}")


def cmd_test(spec: dict, _args) -> None:
    db = open_store(spec)
    items = plan(spec)
    answers = asyncio.run(fill(spec, db, items))
    check = Checks()
    all_answers = [answers]

    for qid, q in spec["questions"].items():
        conf = spec.get("tests", {}).get(qid, {})
        its = [it for it in items if it["qid"] == qid]
        print(f"\n{qid} ({q['type']}, {len(its)} rows)")
        pairs = [o for it in its if (o := outcome(it, answers[it["key"]]))]

        if q["type"] == "choice" and q.get("gold"):
            acc = accuracy(items, answers, qid)
            check(acc >= conf.get("min_accuracy", 0), f"accuracy {acc:.1%} (min {conf.get('min_accuracy', 0):.0%})")

        if pairs:
            ece, table = calibration(pairs)
            check(ece <= conf.get("max_calibration_error", 1), f"calibration error {ece:.3f} (max {conf.get('max_calibration_error', 1)})")
            print("         stated p      n   avg stated   observed")
            for lo, hi, n, c, a in table:
                print(f"       {lo:.1f}–{hi:.1f}  {n:>6}   {c:>10.3f}   {a:>8.3f}")

        if pairs and q["type"] == "choice":
            print("       dial   automated   wrong among automated")
            for t in DIAL:
                auto = [h for p, h in pairs if p >= t]
                wrong = (1 - sum(auto) / len(auto)) if auto else 0
                mark = "  ← act" if t == q.get("act") else ""
                print(f"       {t:>4.2f}   {len(auto) / len(pairs):>9.1%}   {wrong:>21.1%}{mark}")
            if "act" in q and "min_act_accuracy" in conf:
                auto = [h for p, h in pairs if p >= q["act"]]
                a = sum(auto) / len(auto) if auto else 1
                check(a >= conf["min_act_accuracy"],
                      f"accuracy among auto-acted {a:.1%} on {len(auto) / len(pairs):.0%} of rows at act={q['act']} (min {conf['min_act_accuracy']:.0%})")

            wrong = [it for it in its if decide(answers[it["key"]])[0] != it["row"][q["gold"]]]
            wrong.sort(key=lambda it: -decide(answers[it["key"]])[1])
            print(f"       most confident mistakes ({len(wrong)} total; high p + wrong = dangerous, or a gold error):")
            for it in wrong[:SHOW]:
                print(f"         #{it['id']:>4} gold={it['row'][q['gold']]:<32} got {show(answers[it['key']]):<38} {label_of(spec, it['row'], 50)}")
            pairs_confused = Counter((it["row"][q["gold"]], decide(answers[it["key"]])[0]) for it in wrong)
            print("       most confused (gold → got):")
            for (g, got), n in pairs_confused.most_common(8):
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
        col = q.get("gold")
        if col:
            fixed = sum(decide(na)[0] == n["row"][col] for n, _, na, _ in flips)
            broke = sum(decide(oa)[0] == n["row"][col] for n, oa, _, _ in flips)
            p = sign_test(fixed, broke)
            verdict = "significant" if p < 0.05 else "NOT significant: could be noise, get more gold rows"
            print(f"  gold accuracy {accuracy(old_items, old_a, qid):.1%} → {accuracy(new_items, new_a, qid):.1%}"
                  f"  (✓ {fixed} fixed, ✗ {broke} broken, {len(flips) - fixed - broke} wrong either way)"
                  f"\n  paired sign test p={p:.3f} → {verdict}")
        flips.sort(key=lambda f: f[3])  # real flips first, noise-band flips last
        for n, oa, na, noisy in flips[:SHOW]:
            gold = n["row"].get(col or "", "")
            mark = "✓" if gold and decide(na)[0] == gold else ("✗" if gold and decide(oa)[0] == gold else " ")
            print(f"  {mark} #{n['id']:>4} {show(oa):>36} → {show(na):<36}{' ~noise' if noisy else '       '} {label_of(spec, n['row'], 40)}")
        if len(flips) > SHOW:
            print(f"  … {len(flips) - SHOW} more")


# ---------- online ----------

_specs: dict[Path, dict] = {}


async def ajudge(spec_path: str | Path, **fields) -> dict:
    """Judge one row inside an app. Same spec, same keys as batch: a row the batch already judged is a cache hit,
    and a row judged online is a cache hit for the next batch run."""
    path = Path(spec_path).resolve()
    spec = _specs.get(path) or _specs.setdefault(path, load_spec(path))
    items = plan(spec, [fields])
    db = open_store(spec)  # short-lived: DuckDB allows one writer process at a time
    try:
        hits = cached(db, [it["key"] for it in items]).keys()
        answers = await fill(spec, db, items)
    finally:
        db.close()
    out = {}
    for it in items:
        label, p, _ = decide(answers[it["key"]])
        out[it["qid"]] = {"label": label, "p": p, "route": route(it["q"], p), "cached": it["key"] in hits}
    return out


def judge(spec_path: str | Path, **fields) -> dict:
    return asyncio.run(ajudge(spec_path, **fields))


def main() -> None:
    p = argparse.ArgumentParser(prog="hunch")
    p.add_argument("command", choices=["compile", "run", "test", "diff"])
    p.add_argument("spec", type=Path)
    p.add_argument("--against", help="old spec path, or git:REF")
    p.add_argument("--source", type=Path, help="run on this CSV instead of the spec's source (e.g. a holdout set)")
    args = p.parse_args()
    spec = load_spec(args.spec)
    if args.source:
        spec["source"] = args.source.resolve()
    {"compile": cmd_compile, "run": cmd_run, "test": cmd_test, "diff": cmd_diff}[args.command](spec, args)


if __name__ == "__main__":
    main()
