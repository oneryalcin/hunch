# /// script
# requires-python = ">=3.12"
# dependencies = ["httpx", "pyyaml"]
# ///
"""hunch prototype: YAML judgments over a CSV, content-addressed cache, tests, backtest diff, review, online judge.

Throwaway. One file on purpose. See docs/04-design.md for what this is exploring.

PATH is a spec file (a one-node project) or a directory of specs (a project: judgments that ref() each other).

  uv run hunch.py lint    PATH
  uv run hunch.py compile PATH
  uv run hunch.py run     PATH
  uv run hunch.py test    PATH
  uv run hunch.py diff    PATH --against OLD_PATH | git:REF [--node NAME]
  uv run hunch.py review  PATH [--node NAME] [--list] [--limit N] [--audit N]
  (all take --source CSV to run the root judgments on other rows, e.g. a holdout)

  from hunch import judge, ajudge      # online: same project, same cache keys as batch
"""

import argparse
import ast
import asyncio
import csv
import getpass
import hashlib
import itertools
import json
import math
import operator
import os
import random
import re
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
SPEC_KEYS = {"judgment", "model", "source", "key", "state", "questions", "tests", "where", "union", "question", "reviews", "weights", "chain"}
TEST_KEYS = {"min_accuracy", "max_calibration_error", "min_act_accuracy", "min_auroc", "order_stability"}
REQUEST_OVERHEAD_TOKENS = 275  # measured on jev-1.13.0: fixed input tokens per request beyond ~chars/4
CONCURRENCY = 16
NOISE = 0.10  # measured run-to-run sd ~0.03 on ambiguous choices; flips inside this margin are flagged
DIAL = [0.5, 0.6, 0.7, 0.8, 0.9, 0.95, 0.99]
SHOW = 12  # rows listed per section; summaries always cover everything
MAX_COST: float | None = None  # --max-cost: refuse to ask if a single fill would cost more (USD, estimated)
RESERVED = {"answers"}  # the store's own table; a judgment of that name would drop the cache when materialized
REVIEW_FIELDS = ["qid", "row_id", "state_hash", "verdict", "label", "reviewer", "at", "kind"]
# kind = why the row was reviewed: "audit" (random sample of agreements) | "disputed" | "uncertain". Only audits may
# stand in for unreviewed agreeing rows; rows picked for any other reason are not a random sample of anything.


# ---------- spec ----------

class SpecLoader(yaml.SafeLoader):
    """YAML 1.1 reads yes/no/on/off as booleans (the "Norway problem"): `act: {yes: .9, no: .8}` became
    {True: .9, False: .8}, and options named on/off/NO silently collapsed into two keys. Specs keep them as text;
    only true/false are booleans."""


SpecLoader.yaml_implicit_resolvers = {
    k: [(tag, rx) for tag, rx in v if tag != "tag:yaml.org,2002:bool"] for k, v in yaml.SafeLoader.yaml_implicit_resolvers.items()}
SpecLoader.add_implicit_resolver("tag:yaml.org,2002:bool", re.compile(r"^(?:true|True|TRUE|false|False|FALSE)$"), list("tTfF"))


def load_spec(path: Path, text: str | None = None) -> dict:
    spec = yaml.load(text if text is not None else path.read_text(), Loader=SpecLoader)
    if "model" in spec and spec["model"].endswith("latest"):
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


# ---------- project: judgments that ref() each other ----------

REF = re.compile(r"""^ref\(\s*['"]?([\w.-]+)['"]?\s*\)$""")


def upstream(spec: dict) -> list[str]:
    if "union" in spec:
        return list(spec["union"])
    m = REF.match(str(spec.get("source", "")))
    return [m.group(1)] if m else []


def load_project(path: Path, texts: dict[str, str] | None = None) -> dict:
    """A spec file is a one-node project; a directory is every *.yml in it. `texts` (name → yaml) loads an old
    version (git) against today's files. Nodes run in dependency order."""
    path = path.resolve()
    files = [path] if path.is_file() or path.suffix == ".yml" else sorted(path.glob("*.yml"))
    if texts is not None:
        files = [path] if path.suffix == ".yml" else [path / name for name in sorted(texts)]
    nodes = {}
    for f in files:
        spec = load_spec(f, texts.get(f.name) if texts is not None else None)
        if spec["judgment"] in nodes:
            sys.exit(f"two specs are both named {spec['judgment']!r} ({f.name} and another); judgment names must be unique")
        nodes[spec["judgment"]] = spec
    order, state = [], {}

    def visit(name: str, trail: list[str]) -> None:
        if state.get(name) == "done":
            return
        if state.get(name) == "active":
            sys.exit(f"cycle: {' → '.join(trail + [name])}")
        if name not in nodes:
            sys.exit(f"{trail[-1]}: refers to unknown judgment {name!r} (have {sorted(nodes)})")
        state[name] = "active"
        for up in upstream(nodes[name]):
            visit(up, trail + [name])
        state[name] = "done"
        order.append(name)

    for name in nodes:
        visit(name, [])
    for name in order:  # defaults a downstream node inherits from its first upstream
        spec, ups = nodes[name], upstream(nodes[name])
        if ups:
            spec.setdefault("key", nodes[ups[0]]["key"])
    return {"path": path, "nodes": nodes, "order": order}


def load_project_ref(ref: str, current: Path) -> dict:
    """Old version of a project: `git:REF` (as committed) or another path. Run against today's data."""
    current = current.resolve()
    if not ref.startswith("git:"):
        old = load_project(Path(ref))
        home = current if current.is_dir() else current.parent
        for spec in old["nodes"].values():  # today's folder: relative sources, reviews and the store all resolve here
            spec["_dir"] = home
        return old
    rev, folder = ref[4:], current if current.is_dir() else current.parent
    names = [current.name] if current.is_file() else [
        n for n in subprocess.run(["git", "-C", str(folder), "ls-tree", "--name-only", rev, "./"],
                                  check=True, capture_output=True, text=True).stdout.split() if n.endswith(".yml")]
    texts = {n: subprocess.run(["git", "-C", str(folder), "show", f"{rev}:./{n}"],
                               check=True, capture_output=True, text=True).stdout for n in names}
    return load_project(current, texts)


# ---------- where: a safe filter over row columns ----------

_CMP = {ast.Eq: operator.eq, ast.NotEq: operator.ne, ast.Lt: operator.lt, ast.LtE: operator.le, ast.Gt: operator.gt,
        ast.GtE: operator.ge, ast.In: lambda a, b: a in b, ast.NotIn: lambda a, b: a not in b}
_ALLOWED = (ast.Expression, ast.BoolOp, ast.And, ast.Or, ast.UnaryOp, ast.Not, ast.USub, ast.Compare, ast.Name, ast.Load,
            ast.Constant, ast.List, ast.Tuple, *_CMP)


class Unknown(Exception):
    """A where-clause needs an answer that doesn't exist yet (dry runs: compile)."""


def compile_where(expr: str):
    """(predicate, columns used). Columns, constants, comparisons, `in`, and/or/not; nothing else runs."""
    tree = ast.parse(expr, mode="eval")
    for node in ast.walk(tree):
        if not isinstance(node, _ALLOWED):
            raise ValueError(f"where: {type(node).__name__} not allowed in {expr!r} (use columns, constants, comparisons, and/or/not)")

    def val(node, row):
        if isinstance(node, ast.Constant):
            return node.value
        if isinstance(node, ast.Name):
            if row[node.id] is None:
                raise Unknown(node.id)
            return row[node.id]
        if isinstance(node, (ast.List, ast.Tuple)):
            return [val(e, row) for e in node.elts]
        return ev(node, row)

    def ev(node, row):
        if isinstance(node, ast.BoolOp):
            parts = (ev(v, row) for v in node.values)
            return all(parts) if isinstance(node.op, ast.And) else any(parts)
        if isinstance(node, ast.UnaryOp):
            return -val(node.operand, row) if isinstance(node.op, ast.USub) else not ev(node.operand, row)
        if isinstance(node, ast.Compare):
            left = val(node.left, row)
            for o, c in zip(node.ops, node.comparators):
                right = val(c, row)
                a, b = left, right
                if not isinstance(o, (ast.In, ast.NotIn)):  # CSV values are text: compare as numbers when one side is
                    if isinstance(b, (int, float)) and isinstance(a, str):
                        if not a.strip():
                            return False  # an empty cell matches no numeric condition
                        a = float(a)
                    elif isinstance(a, (int, float)) and isinstance(b, str):
                        if not b.strip():
                            return False
                        b = float(b)
                if not _CMP[type(o)](a, b):
                    return False
                left = right
            return True
        return bool(val(node, row))

    return (lambda row: ev(tree.body, row)), {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}


def answer_columns(spec: dict) -> list[str]:
    """Columns a judgment adds to each row: label, confidence, p(yes) for yes/no, route when it has act."""
    cols = []
    for qid, q in spec.get("questions", {}).items():
        cols += [qid, f"{qid}_p"] + ([f"{qid}_pyes"] if q["type"] == "noul" else []) + ([f"{qid}_route"] if "act" in q else [])
    return cols


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
    return {"row": row, "id": str(row.get(spec["key"], "online")), "qid": qid, "rid": qid + variant, "spec": spec,
            "q": q, "aq": aq, "state": state, "shash": digest(state)[:16],
            "key": digest({"model": spec["model"], "state": state, "question": aq})}


def plan(spec: dict, rs: list[dict] | None = None) -> list[dict]:
    return [item(spec, r, qid) for r in (rows(spec) if rs is None else rs) for qid in spec["questions"]]


def label_of(spec: dict, row: dict, width: int = 60) -> str:
    text = " | ".join(row[c] for c in spec["state"]).replace("\n", " ")
    return text if len(text) <= width else text[: width - 1] + "…"


# ---------- lint ----------

def lint_node(spec: dict, header: list[str] | None) -> tuple[list[str], list[str]]:
    """(errors, warnings) for one judgment, given the columns that reach it. Rules come from API limits and from
    what the prototype measured."""
    errors, warnings = [], []
    for k in set(spec) - SPEC_KEYS - {"_dir"}:
        warnings.append(f"unknown spec key {k!r} (typo?)")
    if spec["judgment"] in RESERVED or spec["judgment"].startswith(("_", "sqlite_")):
        errors.append(f"judgment name {spec['judgment']!r} is reserved (the store uses it)")
    if spec.get("chain") and "where" not in spec:
        errors.append("chain: true needs a where-clause over an upstream judgment's answers (it is what gets chained)")
    if header is not None:
        for col in answer_columns(spec):
            if col in header:
                errors.append(f"answer column {col!r} would overwrite an input column of the same name "
                              f"(e.g. a question named like its gold column); rename the question")
        for col in [spec["key"], *spec["state"]]:
            if col not in header:
                errors.append(f"column {col!r} does not reach this judgment (has {header})")
        if "where" in spec:
            try:
                _, used = compile_where(spec["where"])
                for col in sorted(used - set(header)):
                    errors.append(f"where uses {col!r}, which does not reach this judgment")
            except (ValueError, SyntaxError) as e:
                errors.append(str(e))
    for qid, q in spec["questions"].items():
        for k in set(q) - QUESTION_KEYS:
            warnings.append(f"{qid}: unknown key {k!r} (typo?)")
        if "act" in q:
            act = q["act"]
            if isinstance(act, dict):
                if q["type"] != "noul" or set(act) != {"yes", "no"}:
                    errors.append(f"{qid}: act as a mapping must be {{yes: …, no: …}} on a noul question")
                elif not all(isinstance(v, (int, float)) and 0 < v <= 1 for v in act.values()):
                    errors.append(f"{qid}: act thresholds must be in (0, 1], got {act}")
            elif not isinstance(act, (int, float)) or not 0 < act <= 1:
                errors.append(f"{qid}: act must be in (0, 1], got {act}")
        if header is not None and q.get("gold") and q["gold"] not in header:
            # normal for production rows (no gold yet); a warning still catches a typo in the column name
            warnings.append(f"{qid}: gold column {q['gold']!r} does not reach this judgment; these rows have no gold")
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


def lint(project: dict) -> tuple[list[str], list[str]]:
    """Lint every judgment, tracking which columns flow along each ref() so where-clauses and state are
    checked before anything runs."""
    errors, warnings, columns = [], [], {}
    for name in project["order"]:
        spec, ups = project["nodes"][name], upstream(project["nodes"][name])
        tag = lambda xs: [f"{name}: {x}" for x in xs]  # noqa: E731
        if "union" in spec:
            branches = [project["nodes"][u] for u in ups]
            for b in branches:
                q = b.get("questions", {}).get(spec.get("question"))
                if q is None:
                    errors += tag([f"branch {b['judgment']!r} has no question {spec.get('question')!r}"])
                elif q["type"] != branches[0]["questions"].get(spec.get("question"), q)["type"]:
                    errors += tag([f"branch {b['judgment']!r} asks {spec['question']!r} as {q['type']}, others differently"])
            columns[name] = sorted({c for u in ups for c in columns[u]} | {"_branch"})
            continue
        if ups:
            header = columns[ups[0]]
        else:
            try:
                with open(source_path(spec), newline="") as f:
                    header = next(csv.reader(f))
            except FileNotFoundError:
                errors += tag([f"source not found: {source_path(spec)}"])
                header = None
        e, w = lint_node(spec, header)
        errors, warnings = errors + tag(e), warnings + tag(w)
        if "weights" in spec:
            wt = spec["weights"]
            if ups:
                errors += tag(["weights describe how source rows were sampled; set them on the judgment that reads the file"])
            elif header is not None and wt.get("by") not in header:
                errors += tag([f"weights.by column {wt.get('by')!r} not in the source"])
            elif abs(sum(wt.get("population", {}).values()) - 1) > 0.01:
                errors += tag([f"weights.population shares must sum to 1, got {wt.get('population')}"])
        columns[name] = (header or []) + answer_columns(spec) + ["_path_p"] + (["_w"] if "weights" in spec else [])
    if len(project["nodes"]) == 1:  # a single spec: no need to name it in every message
        name = project["order"][0]
        errors = [x.removeprefix(f"{name}: ") for x in errors]
        warnings = [x.removeprefix(f"{name}: ") for x in warnings]
    return errors, warnings


# ---------- store ----------

_conns: dict[Path, sqlite3.Connection] = {}


def store_path(start: Path) -> Path:
    if os.environ.get("HUNCH_STORE"):
        return Path(os.environ["HUNCH_STORE"]).resolve()
    start = start.resolve()
    for d in [start, *start.parents]:
        if (d / ".hunch" / "store.sqlite").exists():
            return d / ".hunch" / "store.sqlite"
    root = next((d for d in [start, *start.parents] if (d / ".git").exists()), start)
    return root / ".hunch" / "store.sqlite"  # new stores go at the repo root, so sibling projects share one


def open_store(spec: dict) -> sqlite3.Connection:
    """SQLite in WAL mode: many processes (batch runs, apps calling judge()) can share it.
    One connection per process and store; writes are short explicit transactions.
    One store per workspace: $HUNCH_STORE, else the nearest `.hunch/store.sqlite` in this folder or above,
    else a new one here. Answers are content-addressed facts; a store per folder made comparisons across
    projects pay twice."""
    path = store_path(spec["_dir"])
    if path in _conns:
        return _conns[path]
    if not path.exists():  # silently starting empty is how a spec outside the workspace re-pays for cached answers
        print(f"new answer store: {path} (no store in this folder or above; HUNCH_STORE=... to share one)",
              file=sys.stderr)
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


async def fill(spec: dict, db, items: list[dict]) -> tuple[dict, dict]:
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
        est = estimate_tokens(group) * PRICE_PER_INPUT_TOKEN
        if MAX_COST is not None and est > MAX_COST:
            raise SystemExit(f"{spec.get('judgment', '')}: would ask {sum(map(len, group))} answers in {len(group)} requests "
                             f"(~${est:.4f}), above --max-cost ${MAX_COST}; nothing asked")
        print(f"  asking {sum(map(len, group))} answers in {len(group)} requests (~${est:.4f})", file=sys.stderr)
        key = os.environ.get("TYPESAFE_API_KEY") or os.environ["TYPESAFE_AI_API_KEY"]
        async with httpx.AsyncClient(headers={"Authorization": f"Bearer {key}"}, timeout=120) as client:
            sem = asyncio.Semaphore(CONCURRENCY)

            async def one(g: list[dict]) -> None:
                res = await ask(client, sem, g[0]["spec"]["model"], g[0]["state"], {it["rid"]: it["aq"] for it in g})
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
    return have, stats


def merge_stats(*stats: dict) -> dict:
    return {k: sum(s[k] for s in stats) for k in ("cached", "asked", "requests", "tokens", "cost")}


def estimate_tokens(groups: list[list[dict]]) -> float:
    """Input tokens for these requests (one per group of items sharing a row): ~chars/4 plus a measured
    fixed overhead per request. Within ~5% of actual on jev-1.13.0."""
    return (sum(len(json.dumps(g[0]["state"])) + sum(len(json.dumps(it["aq"])) for it in g) for g in groups) / 4
            + REQUEST_OVERHEAD_TOKENS * len(groups))


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
    """Chance a random positive scores above a random negative (ties count half)."""
    wins = sum((p > n) + 0.5 * (p == n) for p in pos for n in neg)
    return wins / (len(pos) * len(neg))


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


# ---------- gold and reviews ----------

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
    return frozenset({("yes" if is_yes(v) else "no") if q["type"] == "noul" else v})


def gold_str(g: frozenset | None) -> str:
    return "|".join(sorted(g)) if g else "-"


def hit(it: dict, a: dict, gold: str = "gold") -> bool:
    return decide(a)[0] in it[gold]


def attach_gold(items: list[dict], reviews: dict) -> None:
    """Effective gold = a review verdict on this exact row text if there is one, else the source column.
    Verdicts: model_right / key_right / labeled / confirmed → that label; both_ok → both labels;
    ambiguous → row dropped from scoring. A verdict on text that has since changed is ignored."""
    for it in items:
        col = it["q"].get("gold")
        it["raw_gold"] = normalize_gold(it["q"], it["row"].get(col, "")) if col else None
        r = reviews.get((it["qid"], it["id"]))
        it["verdict"] = r["verdict"] if r and r["state_hash"] == it["shash"] else None
        it["review_kind"] = (r.get("kind") or "") if it["verdict"] else None
        if it["verdict"] == "ambiguous":
            it["gold"], it["gold_src"] = None, "excluded"
        elif it["verdict"]:
            it["gold"], it["gold_src"] = frozenset(r["label"].split("|")), "review"
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


# ---------- execution ----------

ZERO = {"cached": 0, "asked": 0, "requests": 0, "tokens": 0, "cost": 0.0}


async def aexecute(project: dict, rows_in: list[dict] | None = None, dry: bool = False) -> dict[str, dict]:
    """Run every judgment in dependency order. A judgment's input rows are its source file, `rows_in` (online),
    or its upstream judgment's output rows (every input column plus the upstream answers). `where` drops rows
    before anything is asked. `dry` asks nothing: missing answers are reported, and rows whose where-clause
    depends on a missing answer are kept (an upper bound)."""
    results: dict[str, dict] = {}
    for name in project["order"]:
        spec, ups = project["nodes"][name], upstream(project["nodes"][name])
        if "union" in spec:
            parts = [results[u] for u in ups]
            items = [it for res in parts for it in res["items"] if it["qid"] == spec["question"]]
            dupes = [i for i, c in Counter(it["id"] for it in items).items() if c > 1]
            if dupes:
                sys.exit(f"{name}: rows {dupes[:5]} reach more than one branch; a union needs branches whose where-clauses don't overlap")
            results[name] = {"spec": spec, "rows": [r | {"_branch": u} for u, res in zip(ups, parts) for r in res["rows"]],
                             "items": items, "answers": {k: v for res in parts for k, v in res["answers"].items()},
                             "stats": dict(ZERO), "input": sum(len(res["rows"]) for res in parts), "unknown": 0, "hits": set()}
            continue
        inp = results[ups[0]]["rows"] if ups else (rows_in if rows_in is not None else rows(spec))
        if not ups and "weights" in spec and rows_in is None:
            inp = weigh(inp, spec["weights"])
        keep, unknown = inp, 0
        if "where" in spec:
            pred, _ = compile_where(spec["where"])
            keep = []
            for r in inp:
                try:
                    ok = pred(r)
                except Unknown:  # only a dry run has missing answers: keep the row (upper bound)
                    ok, unknown = dry, unknown + dry
                if ok:
                    keep.append(r)
        path_ps = [1.0] * len(keep)
        if spec.get("chain"):  # once per hop: P(this hop's where-clause) × the upstream row's own path
            pred, used = compile_where(spec["where"])
            up = results[ups[0]]
            reads = used & set(up["spec"].get("questions", {}))
            by_row: dict[str, dict] = {}
            for it in up["items"]:
                if it["qid"] in reads:
                    by_row.setdefault(it["id"], {})[it["qid"]] = up["answers"].get(it["key"])
            key_col = up["spec"]["key"]
            path_ps = [r.get("_path_p", 1.0) * p_where(pred, r, by_row.get(str(r.get(key_col)), {})) for r in keep]
        items = plan(spec, keep)
        nq = len(spec["questions"])
        for i, it in enumerate(items):
            it["path_p"] = path_ps[i // nq]
        db = open_store(spec)
        if dry:
            answers = cached(db, [it["key"] for it in items])
            hits, stats = set(answers), {**ZERO, "cached": len(answers)}
        else:
            hits = set(cached(db, [it["key"] for it in items]))
            answers, stats = await fill(spec, db, items)
        out_rows = []
        for i, r in enumerate(keep):
            out = dict(r)
            out["_path_p"] = round(path_ps[i], 4)
            for it in items[i * nq:(i + 1) * nq]:
                a, qid = answers.get(it["key"]), it["qid"]
                label = decide(a)[0] if a else None
                out[qid], out[f"{qid}_p"] = label, round(conf_of(it, a), 3) if a else None
                if it["q"]["type"] == "noul":
                    out[f"{qid}_pyes"] = round(a["noul"], 3) if a else None
                if "act" in it["q"]:
                    out[f"{qid}_route"] = route(it["q"], a, it["path_p"]) if a else None
            out_rows.append(out)
        results[name] = {"spec": spec, "rows": out_rows, "items": items, "answers": answers, "stats": stats,
                         "input": len(inp), "unknown": unknown, "hits": hits}
    return results


def weigh(rs: list[dict], wt: dict) -> list[dict]:
    """Sampling weights: the source over- or under-samples some kind of row (e.g. a 50/50 eval set of a population
    that is 17% "yes"). Each row gets `_w` = population share / sample share of its value in `by`. Rows carry the
    weight downstream, so every judgment is scored on its own sub-population: runs that claim a fix pass ~24% of
    the time, not 17%, and a per-question base rate could not know that."""
    counts = Counter(str(r[wt["by"]]) for r in rs)
    pop = {str(k): v for k, v in wt["population"].items()}
    missing = set(counts) - set(pop)
    if missing:
        sys.exit(f"weights: values {sorted(missing)} of {wt['by']!r} have no population share")
    return [r | {"_w": pop[str(r[wt["by"]])] / (counts[str(r[wt["by"]])] / len(rs))} for r in rs]


def execute(project: dict, **kw) -> dict[str, dict]:
    return asyncio.run(aexecute(project, **kw))


def pick(project: dict, name: str | None) -> str:
    if name:
        if name not in project["nodes"]:
            sys.exit(f"no judgment {name!r}; have {project['order']}")
        return name
    if len(project["nodes"]) == 1:
        return project["order"][0]
    sys.exit(f"this project has several judgments; pick one with --node ({', '.join(project['order'])})")


def question_of(spec: dict) -> list[str]:
    return [spec["question"]] if "union" in spec else list(spec["questions"])


# ---------- commands ----------

def cmd_lint(project: dict, _args) -> None:
    print(f"lint: ok ({len(project['nodes'])} judgment{'s' if len(project['nodes']) > 1 else ''})")  # main() printed findings


def cmd_compile(project: dict, args) -> None:
    results = execute(project, dry=True)
    first = next(n for n in project["order"] if "union" not in project["nodes"][n])
    name = args.node or first
    its = results[name]["items"]
    if its:
        row_items = [it for it in its if it["id"] == its[0]["id"]]
        payload = {"model": row_items[0]["spec"]["model"], "state": row_items[0]["state"],
                   "questions": {it["qid"]: it["aq"] for it in row_items}}
        print(f"# {name}: request for row {its[0]['id']} (one request per row, all questions read once)")
        print(json.dumps(payload, indent=2, ensure_ascii=False))
    total = 0.0
    print()
    for n in project["order"]:
        res, spec = results[n], project["nodes"][n]
        if "union" in spec:
            print(f"# {n}: union of {', '.join(spec['union'])} ({len(res['rows'])} rows)")
            continue
        todo = [it for it in res["items"] if it["key"] not in res["hits"]]
        groups: dict[str, list[dict]] = {}
        for it in todo:
            groups.setdefault(it["id"], []).append(it)
        est = estimate_tokens(list(groups.values()))
        total += est
        bound = "≤ " if res["unknown"] else ""
        where = f", where keeps {bound}{len(res['rows'])}" if "where" in spec else ""
        print(f"# {n}: {res['input']} rows in{where}; {len(res['items'])} answers planned, {len(res['items']) - len(todo)} cached, "
              f"{bound}{len(todo)} to ask, ~{est:,.0f} input tokens, ~${est * PRICE_PER_INPUT_TOKEN:.5f}")
        if res["unknown"]:
            print(f"#   {res['unknown']} rows kept because the answers their where-clause needs aren't cached yet (upper bound)")
    if len(project["nodes"]) > 1:
        print(f"# total: ~{total:,.0f} input tokens, ~${total * PRICE_PER_INPUT_TOKEN:.5f}")


def materialize(spec: dict, db, out_rows: list[dict]) -> None:
    """The judgment's table: its input columns plus its answers, like a dbt model's select *."""
    cols = list(dict.fromkeys(c for r in out_rows for c in r))
    typed = ", ".join(f'"{c}" {"real" if c.endswith(("_p", "_pyes")) else "text"}' for c in cols)
    table = spec["judgment"]
    db.execute("begin immediate")
    try:
        db.execute(f'drop table if exists "{table}"')
        db.execute(f'create table "{table}" ({typed})')
        db.executemany(f'insert into "{table}" values ({", ".join("?" * len(cols))})', [[r.get(c) for c in cols] for r in out_rows])
        db.execute("commit")
    except BaseException:
        db.execute("rollback")
        raise


def print_stats(s: dict, prefix: str = "answers") -> None:
    print(f"{prefix}: {s['cached']} cached, {s['asked']} asked in {s['requests']} requests, "
          f"{s['tokens']:,} input tokens, ${s['cost']:.5f}")


def cmd_run(project: dict, _args) -> None:
    results = execute(project)
    for n in project["order"]:
        res, spec = results[n], project["nodes"][n]
        materialize(spec, open_store(spec), res["rows"])
        if "union" in spec:
            print(f"{n}: union of {len(spec['union'])} judgments → table \"{n}\" ({len(res['rows'])} rows)")
            continue
        where = f", where kept {len(res['rows'])}" if "where" in spec else ""
        print_stats(res["stats"], f"{n}: {res['input']} rows in{where}; answers")
        for qid, q in spec["questions"].items():
            if "act" in q:
                k = sum(1 for r in res["rows"] if r.get(f"{qid}_route") == "review")
                print(f"  {qid}: {k} rows below act={q['act']} → review queue")
    if len(project["nodes"]) > 1:
        print_stats(merge_stats(*(r["stats"] for r in results.values())), "total")
    print(f"materialized {len(project['nodes'])} table(s) in .hunch/store.sqlite")


def accuracy(items: list[dict], answers: dict, qid: str, gold: str = "gold") -> float | None:
    its = [it for it in items if it["qid"] == qid and it[gold]]
    return sum(hit(it, answers[it["key"]], gold) for it in its) / len(its) if its else None


def calib_pairs(its: list[dict], answers: dict, gold: str = "gold", weights: dict | None = None) -> list[tuple]:
    """(stated probability, did it happen, weight). choice: top p vs label in gold; noul: p(yes) vs "yes" in gold."""
    out = []
    for it in its:
        a, g = answers[it["key"]], it[gold]
        if not g:
            continue
        w = weights.get(it["id"], 1.0) if weights else 1.0
        if a["type"] == "choice":
            label, _, _ = decide(a)
            out.append((conf_of(it, a), label in g, w))
        elif a["type"] == "noul":
            out.append((a["noul"], "yes" in g, w))
    return out


def estimate_accuracy(its: list[dict], answers: dict) -> tuple[float, float, float, dict] | str:
    """Accuracy corrected by reviews, with a 95% interval. Rows are split by whether the model agreed with the
    source answer key: agreements are many (audit a random sample), disagreements few (review them all).
    Each group's reviewed rows estimate that group; groups are weighted by size. A fully reviewed group is exact.
    Returns a reason instead of an estimate unless agreements have an audit and *every* disagreement is reviewed:
    reviews made for another judgment (shared gold) are not a random sample of this one's disagreements
    (BANKING77 tree: the 24 it never had reviewed were almost all its own errors; extrapolating said 89.1%)."""
    groups: dict[str, list[dict]] = {"agree": [], "disagree": []}
    for it in its:
        if it["raw_gold"] and it["gold_src"] != "excluded":
            groups["agree" if hit(it, answers[it["key"]], "raw_gold") else "disagree"].append(it)
    total = sum(map(len, groups.values()))
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
        k = sum(hit(it, answers[it["key"]]) for it in reviewed)
        p = k / len(reviewed)
        l, h = (p, p) if len(reviewed) >= len(members) else wilson(k, len(reviewed))
        w = len(members) / total
        est, lo, hi = est + w * p, lo + w * l, hi + w * h
    return est, lo, hi, detail


class Checks:
    """Collects PASS/FAIL lines so `test` can exit non-zero."""
    failed = False

    def __call__(self, ok: bool, text: str) -> None:
        self.failed |= not ok
        print(f"  {'PASS' if ok else 'FAIL'} {text}")


def print_dial(q: dict, rows_: list[tuple]) -> None:
    """rows_: (answer, correct, weight, path confidence). Share of rows acted on automatically at each threshold,
    and how many of those are wrong. Yes/no questions get one column per side: the two sides are separate decisions."""
    total = sum(r[2] for r in rows_)

    def side(label: str | None, t: float) -> tuple[float, float]:
        auto = [(c, w) for a, c, w, pp in rows_ if decide(a)[1] * pp >= t and (label is None or decide(a)[0] == label)]
        ws = sum(w for _, w in auto)
        return ws / total, (sum(w for c, w in auto if not c) / ws if ws else 0.0)

    if q["type"] == "noul":
        print("       dial   act on yes: automated  wrong   │  act on no: automated  wrong")
        for t in DIAL:
            (ya, yw), (na, nw) = side("yes", t), side("no", t)
            mark = "".join(f"  ← act {s}" for s in ("yes", "no") if act_needed(q, s) == t)
            print(f"       {t:>4.2f}   {ya:>20.1%}  {yw:>5.1%}   │  {na:>19.1%}  {nw:>5.1%}{mark}")
    else:
        print("       dial   automated   wrong among automated")
        for t in DIAL:
            auto, wrong = side(None, t)
            print(f"       {t:>4.2f}   {auto:>9.1%}   {wrong:>21.1%}{'  ← act' if act_needed(q, '') == t else ''}")


def it_spec_chain(its: list[dict]) -> bool:
    return any(it["spec"].get("chain") for it in its)


def test_question(spec: dict, qid: str, its: list[dict], answers: dict, check: "Checks", all_stats: list) -> None:
    conf = (spec.get("tests") or {}).get(qid, {})
    q = its[0]["q"] if its else spec["questions"][qid]
    print(f"\n{qid} ({q['type']}, {len(its)} rows)")
    src = Counter(it["gold_src"] for it in its)
    gold_its = [it for it in its if it["gold"]]
    if not gold_its:
        return
    both = sum(len(it["gold"]) > 1 for it in gold_its)
    print(f"  gold: {len(gold_its)} rows ({src['source']} from source, {src['review']} from review"
          f"{f', {both} with two acceptable labels' if both else ''}"
          f"{f', {src['excluded']} excluded as ambiguous' if src['excluded'] else ''})")
    reviewed = src["review"] + src["excluded"] > 0

    weights, note = None, ""
    if any("_w" in it["row"] for it in gold_its):
        weights = {it["id"]: it["row"]["_w"] for it in gold_its}
        tw = sum(weights.values())
        yes = sum(weights[it["id"]] for it in gold_its if "yes" in it["gold"]) / tw if q["type"] == "noul" else None
        note = " [weighted to the population]"
        print(f"  weighted to the population (source sampling weights)"
              + (f": {yes:.0%} yes among these rows, {sum('yes' in it['gold'] for it in gold_its) / len(gold_its):.0%} in the sample" if yes is not None else ""))
    w = (lambda it: weights[it["id"]]) if weights else (lambda it: 1.0)

    acc = sum(w(it) * hit(it, answers[it["key"]]) for it in gold_its) / sum(w(it) for it in gold_its)
    want = conf.get("min_accuracy", 0)
    est = estimate_accuracy(its, answers) if not weights else "not yet implemented with sampling weights"
    if not isinstance(est, str):
        # Headline = the estimate. Accuracy on "current gold" trusts every unreviewed row, so once
        # disagreements are corrected it only errs upward.
        e, lo, hi, d = est
        check(e >= want, f"estimated accuracy {e:.1%} (95% CI {lo:.1%}–{hi:.1%}) from reviews of "
              + ", ".join(f"{n}/{m} {g}ing rows" for g, (n, m) in d.items()) + f" (min {want:.0%})")
        print(f"       not the headline: on current gold {acc:.1%} (trusts unreviewed rows), "
              f"on the raw answer key {accuracy(its, answers, qid, 'raw_gold'):.1%}")
    else:
        raw = f" (raw source gold: {accuracy(its, answers, qid, 'raw_gold'):.1%})" if reviewed else ""
        check(acc >= want, f"accuracy {acc:.1%}{raw}{note} (min {want:.0%})")
        if reviewed:
            print(f"       upper bound only, no estimate: {est}")

    ece, table = calibration(calib_pairs(its, answers, weights=weights))
    raw = f" (raw source gold: {calibration(calib_pairs(its, answers, 'raw_gold', weights))[0]:.3f})" if reviewed else ""
    check(ece <= conf.get("max_calibration_error", 1), f"calibration error {ece:.3f}{raw}{note} (max {conf.get('max_calibration_error', 1)})")
    print(f"         {'stated p(yes)' if q['type'] == 'noul' else 'stated p':<13} {'n':>5}   avg stated   observed")
    for lo_, hi_, n, c, a in table:
        print(f"       {lo_:.1f}–{hi_:.1f}      {n:>6}   {c:>10.3f}   {a:>8.3f}")

    if q["type"] == "noul":
        pos = [answers[it["key"]]["noul"] for it in gold_its if "yes" in it["gold"]]
        neg = [answers[it["key"]]["noul"] for it in gold_its if "no" in it["gold"]]
        if pos and neg:
            auc = auroc(pos, neg)
            check(auc >= conf.get("min_auroc", 0),
                  f"AUROC {auc:.3f} ({len(pos)} yes / {len(neg)} no; 0.5 = coin toss; unaffected by base rate) (min {conf.get('min_auroc', 0)})")

    offered = [it for it in gold_its if it["aq"].get("criteria") and isinstance(it["aq"]["criteria"], dict)]
    lost = [it for it in offered if not it["gold"] & set(it["aq"]["criteria"])]
    if lost:
        print(f"       {len(lost)} of {len(offered)} rows' gold is not among the options this judgment offered "
              f"(sent to the wrong place upstream; no answer here could be right)")
    if it_spec_chain(its):
        own = [(decide(answers[it["key"]])[1], hit(it, answers[it["key"]])) for it in gold_its]
        chn = [(conf_of(it, answers[it["key"]]), h) for it, (_, h) in zip(gold_its, own)]
        sep = lambda xs: auroc([c for c, h in xs if h], [c for c, h in xs if not h]) if 0 < sum(h for _, h in xs) < len(xs) else float("nan")  # noqa: E731
        print(f"       confidence is chained (× P(routed here correctly)); it separates right from wrong answers "
              f"with AUROC {sep(chn):.3f}, own confidence alone {sep(own):.3f}")
    scored = [(answers[it["key"]], hit(it, answers[it["key"]]), w(it), it.get("path_p", 1.0)) for it in gold_its]
    print_dial(q, scored)
    if "act" in q and "min_act_accuracy" in conf:
        acted = [(c, wt) for (a, c, wt, pp) in scored if route(q, a, pp) == "act"]
        ws = sum(wt for _, wt in acted)
        a_acc = sum(wt for c, wt in acted if c) / ws if ws else 1.0
        check(a_acc >= conf["min_act_accuracy"],
              f"accuracy among auto-acted {a_acc:.1%} on {ws / sum(sc[2] for sc in scored):.0%} of rows at act={q['act']} (min {conf['min_act_accuracy']:.0%})")

    wrong = sorted((it for it in gold_its if not hit(it, answers[it["key"]])), key=lambda it: -conf_of(it, answers[it["key"]]))
    print(f"       most confident mistakes ({len(wrong)} total; high confidence + wrong = dangerous, or a gold error → hunch review):")
    for it in wrong[:SHOW]:
        got = f"{decide(answers[it['key']])[0]} {conf_of(it, answers[it['key']]):.2f}"
        print(f"         #{it['id']:>4} gold={gold_str(it['gold']):<32} got {got:<38} {label_of(it['spec'], it['row'], 50)}")
    print("       most confused (gold → got):")
    for (g, got), n in Counter((gold_str(it["gold"]), decide(answers[it["key"]])[0]) for it in wrong).most_common(8):
        print(f"         {n:>3}  {g} → {got}")

    order = conf.get("order_stability")
    if order and q["type"] == "choice":
        base = sample(its, order.get("sample", 100))
        variants = [item(it["spec"], it["row"], qid, permuted(it["aq"], k), f"~p{k}")
                    for k in range(1, order.get("permutations", 2) + 1) for it in base]
        vans, vstats = asyncio.run(fill(base[0]["spec"], open_store(base[0]["spec"]), variants))
        all_stats.append(vstats)
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
            print(f"         #{b['id']:>4} {v['rid']:<14} {show(answers[b['key']]):<36} → {show(vans[v['key']]):<36} {label_of(b['spec'], b['row'], 40)}")


def cmd_test(project: dict, args) -> None:
    results = execute(project)
    check = Checks()
    all_stats = [r["stats"] for r in results.values()]
    names = [args.node] if args.node else project["order"]
    for n in names:
        res, spec = results[n], project["nodes"][n]
        if len(project["nodes"]) > 1:
            where = f", where kept {len(res['rows'])} of {res['input']}" if "where" in spec else ""
            kind = f"union of {', '.join(spec['union'])}" if "union" in spec else f"{res['input']} rows in{where}"
            print(f"\n══ {n} ({kind})")
        attach_gold(res["items"], load_reviews(spec))  # just before testing: a union shares its branches' items
        for qid in question_of(spec):
            its = [it for it in res["items"] if it["qid"] == qid]
            test_question(spec, qid, its, res["answers"], check, all_stats)
    print()
    print_stats(merge_stats(*all_stats))
    sys.exit(1 if check.failed else 0)


def diff_question(qid: str, new_its: list[dict], old_its: list[dict], new_a: dict, old_a: dict, same_spec: bool) -> None:
    old_by = {it["id"]: it for it in old_its}
    new_ids = {it["id"] for it in new_its}
    pairs = [(old_by[it["id"]], it) for it in new_its if it["id"] in old_by]
    entered, left = len(new_ids - set(old_by)), len(set(old_by) - new_ids)
    moved = f"; {entered} rows newly reach it, {left} no longer do" if entered or left else ""
    if not old_its:
        print(f"\n{qid}: new question ({len(new_its)} rows)")
        return
    if all(o["key"] == n["key"] for o, n in pairs) and not moved:
        print(f"\n{qid}: unchanged (same keys, 0 calls)")
        return
    flips = []
    for o, n in pairs:
        oa, na = old_a[o["key"]], new_a[n["key"]]
        (ol, _, om), (nl, _, nm) = decide(oa), decide(na)
        if ol != nl:
            flips.append((n, oa, na, min(om, nm) < NOISE))
    why = " (its own spec is unchanged: moved by upstream changes)" if same_spec and (flips or moved) else ""
    print(f"\n{qid}: {len(flips)}/{len(pairs)} rows flip ({sum(f[3] for f in flips)} within noise band){moved}{why}")
    if flips and any(n["gold"] for _, n in pairs):
        fixed = sum(hit(n, na) and not hit(n, oa) for n, oa, na, _ in flips if n["gold"])
        broke = sum(hit(n, oa) and not hit(n, na) for n, oa, na, _ in flips if n["gold"])
        p = sign_test(fixed, broke)
        verdict = "significant" if p < 0.05 else "NOT significant: could be noise, get more gold rows"
        common = [n for _, n in pairs]
        print(f"  gold accuracy on the {len(common)} shared rows {accuracy([o for o, _ in pairs], old_a, qid):.1%} → "
              f"{accuracy(common, new_a, qid):.1%}  (✓ {fixed} fixed, ✗ {broke} broken, {len(flips) - fixed - broke} other)"
              f"\n  paired sign test p={p:.3f} → {verdict}")
    flips.sort(key=lambda f: f[3])  # real flips first, noise-band flips last
    for n, oa, na, noisy in flips[:SHOW]:
        g = n["gold"]
        mark = "✓" if g and hit(n, na) and not hit(n, oa) else ("✗" if g and hit(n, oa) and not hit(n, na) else " ")
        print(f"  {mark} #{n['id']:>4} {show(oa):>36} → {show(na):<36}{' ~noise' if noisy else '       '} {label_of(n['spec'], n['row'], 40)}")
    if len(flips) > SHOW:
        print(f"  … {len(flips) - SHOW} more")


def cmd_diff(project: dict, args) -> None:
    """Old logic on today's data: the old project's root judgments read the same rows as today's."""
    old = load_project_ref(args.against, args.path)
    new_roots = [n for n in project["order"] if not upstream(project["nodes"][n])]
    for n in old["order"]:
        spec = old["nodes"][n]
        if upstream(spec):
            continue
        twin = n if n in new_roots else (new_roots[0] if len(new_roots) == 1 else None)
        if twin:
            spec["source"] = source_path(project["nodes"][twin]).resolve()
    new_r, old_r = execute(project), execute(old)
    print_stats(merge_stats(*(r["stats"] for r in (*new_r.values(), *old_r.values()))))
    def twin_of(n: str) -> str | None:  # same name, else the only judgment on the other side (a renamed copy)
        return n if n in old["nodes"] else (old["order"][0] if len(old["nodes"]) == 1 else None)
    if args.node:
        pairs = [(args.node, twin_of(args.node))]
    elif len(project["nodes"]) == 1:
        pairs = [(project["order"][0], twin_of(project["order"][0]))]
    else:
        pairs = [(n, n) for n in project["order"] if n in old["nodes"]]
        if not pairs:
            sys.exit(f"no judgment names in common: {project['order']} vs {old['order']}; pick one with --node")
    for n, o in pairs:
        if o is None:
            sys.exit(f"--against has no judgment matching {n!r}; it has {old['order']}")
        spec, ospec = project["nodes"][n], old["nodes"][o]
        same = {k: v for k, v in spec.items() if k != "_dir"} == {k: v for k, v in ospec.items() if k != "_dir"}
        if len(project["nodes"]) > 1 or n != o:
            print(f"\n══ {n}" + (f"  vs  {o}" if n != o else ""))
        reviews = load_reviews(spec)  # gold is about the data, so both sides use today's reviews
        attach_gold(new_r[n]["items"], reviews)
        attach_gold(old_r[o]["items"], reviews)
        old_qs = question_of(ospec)
        for qid in question_of(spec):
            new_its = [it for it in new_r[n]["items"] if it["qid"] == qid]
            oq = qid
            if qid not in old_qs:  # renamed? question ids aren't in the cache key, so match by keys
                keys = {it["key"] for it in new_its}
                oq = next((q for q in old_qs if {it["key"] for it in old_r[o]["items"] if it["qid"] == q} & keys), qid)
                if oq != qid:
                    print(f"\n{qid}: renamed from {oq!r}")
            diff_question(qid, new_its, [it for it in old_r[o]["items"] if it["qid"] == oq],
                          new_r[n]["answers"], old_r[o]["answers"], same)


def review_queue(items: list[dict], answers: dict, audit: int) -> list[tuple[str, dict]]:
    """disputed: model ≠ source answer key (a model error, or a gold error); all of them, most confident first.
    audit: a fixed random sample of rows where model = answer key, topped up to `audit` reviewed rows per question.
      Without it, reviewing only disputes can only move accuracy up.
    uncertain: no gold and below the act threshold (a human label makes it gold).
    Rows with a current verdict are done."""
    disputed, uncertain, agree = [], [], {}
    audited = Counter()
    for it in items:
        a = answers[it["key"]]
        agrees = bool(it["raw_gold"]) and hit(it, a, "raw_gold")
        if it["gold_src"] in ("review", "excluded"):
            audited[it["qid"]] += agrees
            continue
        if it["raw_gold"] and not agrees:
            disputed.append(it)
        elif agrees:
            agree.setdefault(it["qid"], []).append(it)
        elif not it["raw_gold"] and route(it["q"], a, it.get("path_p", 1.0)) == "review":
            uncertain.append(it)
    audits = [it for qid, members in agree.items()
              for it in sorted(members, key=lambda it: digest([it["qid"], it["id"]]))[: max(0, audit - audited[qid])]]
    disputed.sort(key=lambda it: -conf_of(it, answers[it["key"]]))
    uncertain.sort(key=lambda it: conf_of(it, answers[it["key"]]))
    return [("disputed", it) for it in disputed] + [("audit", it) for it in audits] + [("uncertain", it) for it in uncertain]


def cmd_review(project: dict, args) -> None:
    name = pick(project, args.node)
    spec = project["nodes"][name]
    res = execute(project)[name]
    items, answers = res["items"], res["answers"]
    attach_gold(items, load_reviews(spec))
    queue = review_queue(items, answers, args.audit)
    kinds = Counter(k for k, _ in queue)
    print(f"review queue: {kinds['disputed']} disputed (model ≠ answer key), {kinds['audit']} audit (random rows where "
          f"they agree), {kinds['uncertain']} uncertain (below act, no gold) → verdicts go to {reviews_path(spec).name}")
    queue = queue[: args.limit] if args.limit else queue
    if args.list:
        for kind, it in queue:
            print(f"  {kind:<9} #{it['id']:>5} {it['qid']:<10} {show(answers[it['key']]):<36} "
                  f"gold={gold_str(it['raw_gold']):<24} {label_of(it['spec'], it['row'], 50)}")
        return

    reviewer = args.reviewer or getpass.getuser()
    done = 0
    for n, (kind, it) in enumerate(queue, 1):
        a = answers[it["key"]]
        top = ranked(a)[:3]
        key = gold_str(it["raw_gold"])
        options = set(it["aq"].get("criteria") or {}) if a["type"] == "choice" else {"yes", "no"}
        print(f"\n[{kind} {n}/{len(queue)}] #{it['id']}  {it['qid']}")
        for col in it["spec"]["state"]:
            print(f"  {col}: {label_of({'state': [col]}, it['row'], 400)}")
        if kind == "disputed":
            print(f"  answer key: {key}")
            print("  model:      " + "   ".join(f"{i}) {lab} {p:.2f}" for i, (lab, p) in enumerate(top, 1)))
            keys = "[m] model is right   [k] answer key is right   [b] both acceptable   [1-3] pick   "
        elif kind == "audit":
            print(f"  label: {key}")
            print("  other options: " + "   ".join(f"{i}) {lab}" for i, (lab, _) in enumerate(top, 1)))
            keys = "[y] label is right   [1-3] pick the right one   "
        else:
            print("  model:      " + "   ".join(f"{i}) {lab} {p:.2f}" for i, (lab, p) in enumerate(top, 1)))
            keys = "[1-3] pick   "
        print(f"  {keys}[a] ambiguous   [s] skip   [q] quit   or type an option")
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
                verdict, label = "key_right", key
            elif ans == "b" and kind == "disputed":
                verdict, label = "both_ok", f"{key}|{top[0][0]}"
            elif ans == "y" and kind == "audit":
                verdict, label = "confirmed", key
            elif ans in {"1", "2", "3"} and int(ans) <= len(top):
                verdict, label = "labeled", top[int(ans) - 1][0]
            elif ans in options:
                verdict, label = "labeled", ans
            else:
                print("  ? not an option")
                continue
            append_review(spec, {"qid": it["qid"], "row_id": it["id"], "state_hash": it["shash"], "kind": kind,
                                 "verdict": verdict, "label": label or "", "reviewer": reviewer,
                                 "at": datetime.now(timezone.utc).isoformat(timespec="seconds")})
            done += 1
            break
    print(f"\n{done} verdicts saved to {reviews_path(spec).name}")


# ---------- online ----------

_projects: dict[Path, dict] = {}


async def ajudge(path: str | Path, node: str | None = None, **fields) -> dict | None:
    """Judge one row inside an app: the whole project runs on it, same keys as batch (a row the batch already
    judged is a cache hit; a row judged online is a hit for the next batch). Returns {judgment: {question:
    answer}}; a judgment the row never reached (its where-clause said no) is None. For a one-judgment project,
    or with `node`, returns just that judgment's answers."""
    p = Path(path).resolve()
    project = _projects.get(p) or _projects.setdefault(p, load_project(p))
    results = await aexecute(project, rows_in=[fields])
    out: dict[str, dict | None] = {}
    for n in project["order"]:
        res = results[n]
        if "union" in res["spec"]:
            continue
        if not res["rows"]:
            out[n] = None
            continue
        out[n] = {}
        for it in res["items"]:
            a = res["answers"][it["key"]]
            label, conf, _ = decide(a)
            out[n][it["qid"]] = {"label": label, "p": conf_of(it, a), "route": route(it["q"], a, it["path_p"]),
                                 "cached": it["key"] in res["hits"]}
    if node:
        return out[node]
    return next(iter(out.values())) if len(out) == 1 else out


def judge(path: str | Path, node: str | None = None, **fields) -> dict | None:
    return asyncio.run(ajudge(path, node, **fields))


def main() -> None:
    commands = {"lint": cmd_lint, "compile": cmd_compile, "run": cmd_run, "test": cmd_test,
                "diff": cmd_diff, "review": cmd_review}
    p = argparse.ArgumentParser(prog="hunch")
    p.add_argument("command", choices=list(commands))
    p.add_argument("path", type=Path, help="a spec file, or a directory of specs (a project)")
    p.add_argument("--node", help="one judgment in a project (test, diff, review, compile)")
    p.add_argument("--against", help="diff: old spec/project path, or git:REF")
    p.add_argument("--source", type=Path, help="run the root judgments on this CSV instead (e.g. a holdout set)")
    p.add_argument("--list", action="store_true", help="review: print the queue without prompting")
    p.add_argument("--limit", type=int, help="review: at most N items")
    p.add_argument("--audit", type=int, default=30, help="review: random agreeing rows to audit per question (default 30)")
    p.add_argument("--reviewer", help="review: name recorded with each verdict (default: $USER)")
    p.add_argument("--max-cost", type=float, help="refuse to ask if one judgment's missing answers would cost more (USD, estimated)")
    args = p.parse_args()
    global MAX_COST
    MAX_COST = args.max_cost
    project = load_project(args.path)
    if args.source:
        for n in project["order"]:
            if not upstream(project["nodes"][n]):
                project["nodes"][n]["source"] = args.source.resolve()
    errors, warnings = lint(project)
    for w in warnings:
        print(f"lint warning: {w}", file=sys.stderr)
    for e in errors:
        print(f"lint error: {e}", file=sys.stderr)
    if errors:
        sys.exit(2)
    commands[args.command](project, args)


if __name__ == "__main__":
    main()
