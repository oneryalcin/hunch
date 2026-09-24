# /// script
# requires-python = ">=3.12"
# dependencies = ["httpx", "duckdb", "pyyaml"]
# ///
"""hunch prototype: YAML judgments over a CSV, content-addressed cache, backtest diff.

Throwaway. One file on purpose. See docs/04-design.md for what this is exploring.

  uv run hunch.py compile SPEC
  uv run hunch.py run     SPEC
  uv run hunch.py test    SPEC
  uv run hunch.py diff    SPEC --against OLD_SPEC | git:REF
"""

import argparse
import asyncio
import csv
import hashlib
import json
import os
import subprocess
import sys
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


# ---------- spec ----------

def load_spec(path: Path, text: str | None = None) -> dict:
    spec = yaml.safe_load(text if text is not None else path.read_text())
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
    return {col: row[col] for col in spec["state"]}


def cache_key(model: str, state: dict, question: dict) -> str:
    blob = json.dumps({"model": model, "state": state, "question": question}, sort_keys=True)
    return hashlib.sha256(blob.encode()).hexdigest()


def plan(spec: dict) -> list[dict]:
    """One entry per (row, question): the exact key that identifies its answer."""
    out = []
    for row in rows(spec):
        state = state_of(spec, row)
        for qid, q in spec["questions"].items():
            aq = api_question(q)
            out.append({"row": row, "id": row[spec["key"]], "qid": qid, "q": q, "aq": aq,
                        "state": state, "key": cache_key(spec["model"], state, aq)})
    return out


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
    have = cached(db, [it["key"] for it in items])
    missing: dict[str, list[dict]] = {}
    for it in items:
        if it["key"] not in have:
            missing.setdefault(it["id"], []).append(it)

    stats = {"cached": len(items) - sum(map(len, missing.values())), "asked": 0, "requests": len(missing), "tokens": 0}
    if missing:
        key = os.environ.get("TYPESAFE_API_KEY") or os.environ["TYPESAFE_AI_API_KEY"]
        async with httpx.AsyncClient(headers={"Authorization": f"Bearer {key}"}, timeout=60) as client:
            sem = asyncio.Semaphore(CONCURRENCY)
            group = list(missing.values())
            results = await asyncio.gather(*[
                ask(client, sem, spec["model"], g[0]["state"], {it["qid"]: it["aq"] for it in g}) for g in group])
        for g, res in zip(group, results):
            tokens = res["usage"]["input_tokens"]
            stats["tokens"] += tokens
            for it in g:
                a = res["answers"][it["qid"]]
                db.execute("insert or replace into answers (key, model, answer, input_tokens) values (?, ?, ?, ?)",
                           [it["key"], res["model"], json.dumps(a), tokens / len(g)])
                have[it["key"]] = a
                stats["asked"] += 1
    stats["cost"] = stats["tokens"] * PRICE_PER_INPUT_TOKEN
    return have | {"_stats": stats}


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


# ---------- commands ----------

def cmd_compile(spec: dict, _args) -> None:
    items = plan(spec)
    first = items[0]
    payload = {"model": spec["model"], "state": first["state"],
               "questions": {it["qid"]: it["aq"] for it in items if it["id"] == first["id"]}}
    print(f"# request for row {first['id']} (one request per row, all questions read once)")
    print(json.dumps(payload, indent=2))
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


def accuracy(spec: dict, items: list[dict], answers: dict, qid: str) -> float | None:
    col = spec["questions"][qid].get("gold")
    if not col:
        return None
    its = [it for it in items if it["qid"] == qid]
    return sum(decide(answers[it["key"]])[0] == it["row"][col] for it in its) / len(its)


def cmd_test(spec: dict, _args) -> None:
    db = open_store(spec)
    items = plan(spec)
    answers = asyncio.run(fill(spec, db, items))
    print_stats(answers["_stats"])
    failed = False
    for qid, q in spec["questions"].items():
        acc = accuracy(spec, items, answers, qid)
        if acc is None:
            continue
        want = spec.get("tests", {}).get(qid, {}).get("min_accuracy", 0)
        ok = acc >= want
        failed |= not ok
        print(f"  {'PASS' if ok else 'FAIL'} {qid}: accuracy {acc:.1%} (min {want:.0%})")
        for it in items:
            if it["qid"] == qid and decide(answers[it["key"]])[0] != it["row"][q["gold"]]:
                print(f"       #{it['id']:>3} gold={it['row'][q['gold']]:<10} got {show(answers[it['key']])}  {it['row']['subject']}")
    sys.exit(1 if failed else 0)


def cmd_diff(spec: dict, args) -> None:
    old = load_spec_ref(args.against, args.spec)
    db = open_store(spec)
    new_items, old_items = plan(spec), plan(old)
    new_a = asyncio.run(fill(spec, db, new_items))
    old_a = asyncio.run(fill(old, db, old_items))
    s = {k: new_a["_stats"][k] + old_a["_stats"][k] for k in ("cached", "asked", "requests", "tokens", "cost")}
    print_stats(s)

    old_by = {(it["id"], it["qid"]): it for it in old_items}
    for qid, q in spec["questions"].items():
        pairs = [(old_by.get((it["id"], qid)), it) for it in new_items if it["qid"] == qid]
        if not any(o for o, _ in pairs):
            print(f"\n{qid}: new question")
            continue
        same_key = sum(1 for o, n in pairs if o and o["key"] == n["key"])
        if same_key == len(pairs):
            print(f"\n{qid}: unchanged (same keys, 0 calls)")
            continue
        flips = []
        for o, n in pairs:
            oa, na = old_a[o["key"]], new_a[n["key"]]
            (ol, _, om), (nl, _, nm) = decide(oa), decide(na)
            if ol != nl:
                flips.append((n, oa, na, min(om, nm) < NOISE))
        print(f"\n{qid}: {len(flips)}/{len(pairs)} rows flip")
        oacc, nacc = accuracy(old, old_items, old_a, qid), accuracy(spec, new_items, new_a, qid)
        if oacc is not None:
            print(f"  gold accuracy {oacc:.1%} → {nacc:.1%}")
        for n, oa, na, noisy in flips:
            gold = n["row"].get(q.get("gold") or "", "")
            mark = "✓" if gold and decide(na)[0] == gold else ("✗" if gold else " ")
            print(f"  {mark} #{n['id']:>3} {show(oa):>16} → {show(na):<16}{' ~noise' if noisy else ''}  {n['row']['subject']}")


def main() -> None:
    p = argparse.ArgumentParser(prog="hunch")
    p.add_argument("command", choices=["compile", "run", "test", "diff"])
    p.add_argument("spec", type=Path)
    p.add_argument("--against", help="old spec path, or git:REF")
    args = p.parse_args()
    spec = load_spec(args.spec)
    {"compile": cmd_compile, "run": cmd_run, "test": cmd_test, "diff": cmd_diff}[args.command](spec, args)


if __name__ == "__main__":
    main()
