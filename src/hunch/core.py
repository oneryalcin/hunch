"""hunch engine: judgments as YAML specs over rows, a content-addressed answer store, tests, backtest diff, review,
suggest, shadow mode and online judge. The package's public API is in hunch/__init__.py; the CLI is `hunch`.

PATH is a spec file (a one-node project) or a directory of specs (a project: judgments that ref() each other).

  hunch lint    PATH
  hunch compile PATH
  hunch run     PATH
  hunch test    PATH
  hunch diff    PATH --against OLD_PATH | git:REF [--node NAME]
  hunch review  PATH [--node NAME] [--list] [--limit N] [--audit N] [--against OLD]
  hunch suggest PATH [--node NAME] [--question Q] [--n 3] [--writer ENGINE]
  hunch init    RECIPE [DIR]          (hunch init --list)
  (all take --source CSV to run the root judgments on other rows, e.g. a holdout,
   or --traffic for the rows logged in shadow mode, and --model ENGINE to run on another engine)

  from hunch import judge, ajudge      # online: same project, same cache keys as batch
  judge(LIVE, shadow=CANDIDATE, **row) # also answers with the candidate and logs the row; then
                                       # hunch diff CANDIDATE --against LIVE --traffic   (free: all cached)
"""

import argparse
import ast
import asyncio
import concurrent.futures.thread  # noqa: F401  (see _join_shadows: its exit hook must come first)
import csv
import difflib
import getpass
import hashlib
import itertools
import json
import math
import operator
import os
import random
import re
import shutil
import sqlite3
import subprocess
import sys
import textwrap
import threading
import time
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

import httpx
import yaml

from hunch import engines, settings

API = "https://api.typesafe.ai/v1/systemone"
PRICE_PER_INPUT_TOKEN = 0.042 / 1_000_000  # output tokens are free
HUNCH_ONLY_FIELDS = {"act", "gold", "escalate", "baseline", "_multi"}  # routing/test config: never sent, never part of the key
QUESTION_KEYS = {"type", "instructions", "criteria", "none"} | HUNCH_ONLY_FIELDS
NONE = "none_of_these"  # the option `none:` adds to a choice question
SPEC_KEYS = {"judgment", "model", "source", "key", "state", "questions", "tests", "where", "union", "question", "reviews", "metrics", "examples",
             "weights", "chain", "view", "clip", "redact", "on_change", "description", "exposures", "targets"}
UNION_KEYS = {"judgment", "union", "question", "key", "reviews", "metrics", "tests", "description", "exposures", "targets"}
META_KEYS = ("description", "exposures")  # for people and `hunch docs`: never sent, never in a key or spec hash
TARGET_KEYS = {"model", "sample", "store", "max_cost"}  # targets.<name>: how --target <name> runs the spec
EXPOSURE_KEYS = {"name", "kind", "owner", "uses", "url", "description"}
EXPOSURE_KINDS = ("app", "hook", "dashboard", "job")
ON_CHANGE = ("reask", "new_rows_only", "freeze")
TEST_KEYS = {"min_accuracy", "max_calibration_error", "min_act_accuracy", "min_auroc", "min_recall", "order_stability",
             "severity"}
METRIC_TEST_KEYS = {"min_rate", "max_rate", "max_missed", "max_false_alarms", "higher", "severity"}  # tests on a metric, by its name
FEW = 10  # a group with fewer rows than this is reported, but its interval is too wide to say much
SEVERITIES = ("error", "warn")  # a failing check with severity warn prints WARN and does not fail `test`
REQUEST_OVERHEAD_TOKENS = 275  # measured on jev-1.13.0: fixed input tokens per request beyond ~chars/4
CONCURRENCY = int(os.environ.get("HUNCH_CONCURRENCY", 16))  # requests in flight per fill
NOISE = 0.10  # measured run-to-run sd ~0.03 on ambiguous choices; flips inside this margin are flagged
DIAL = [0.5, 0.6, 0.7, 0.8, 0.9, 0.95, 0.99]
SHOW = 12  # rows listed per section; summaries always cover everything


def detail(*args, **kwargs) -> None:
    """The full diagnostic report, requested with --verbose."""
    if settings.VERBOSE:
        print(*args, **kwargs)
RESERVED = {"answers", "traffic"}  # the store's own table; a judgment of that name would drop the cache when materialized
REVIEW_FIELDS = ["qid", "row_id", "state_hash", "verdict", "label", "reviewer", "at", "kind"]
# kind = why the row was reviewed: "audit" (random sample of agreements) | "disputed" | "uncertain". Only audits may
# stand in for unreviewed agreeing rows; rows picked for any other reason are not a random sample of anything.
# attach_gold also marks "same_text": a row given another live row's verdict because their text is identical.


# ---------- spec ----------

class SpecLoader(yaml.SafeLoader):
    """YAML 1.1 reads yes/no/on/off as booleans (the "Norway problem"): `act: {yes: .9, no: .8}` became
    {True: .9, False: .8}, and options named on/off/NO silently collapsed into two keys. Specs keep them as text;
    only true/false are booleans."""


SpecLoader.yaml_implicit_resolvers = {
    k: [(tag, rx) for tag, rx in v if tag != "tag:yaml.org,2002:bool"] for k, v in yaml.SafeLoader.yaml_implicit_resolvers.items()}
SpecLoader.add_implicit_resolver("tag:yaml.org,2002:bool", re.compile(r"^(?:true|True|TRUE|false|False|FALSE)$"), list("tTfF"))


def load_spec(path: Path, text: str | None = None) -> dict:
    try:
        spec = yaml.load(text if text is not None else path.read_text(), Loader=SpecLoader)
    except FileNotFoundError:
        sys.exit(f"{path}: no such file")
    except yaml.YAMLError as e:  # e.g. a regex in double quotes: "\d" is a YAML escape; use single quotes
        sys.exit(f"{path}: not valid YAML: {' '.join(str(e).split())}")
    if not isinstance(spec, dict):
        sys.exit(f"{path}: a spec is a YAML mapping (judgment:, source:, questions: …)")
    # shapes the project graph and question expansion read before lint runs: a clear message, not a traceback
    for k in ("judgment", "model", "source", "where", "question"):
        needed = k == "judgment" or (k == "question" and "union" in spec)
        if (k in spec or needed) and not (isinstance(spec.get(k), str) and spec[k]):
            sys.exit(f"{path}: {k}: is text, got {spec.get(k)!r}")
    if "union" in spec and not (isinstance(spec["union"], list) and spec["union"]
                                and all(isinstance(u, str) for u in spec["union"])):
        sys.exit(f"{path}: union: lists the judgments it combines ([a, b]), got {spec['union']!r}")
    qs = spec.get("questions")
    if "questions" in spec and not (isinstance(qs, dict) and all(isinstance(q, dict) for q in qs.values())):
        sys.exit(f"{path}: questions: maps each name to {{type: …, instructions: …}}, got {qs!r}")
    if "model" in spec and (spec["model"].endswith("latest") or ":~" in spec["model"]):
        sys.exit(f"{path}: pin an exact model version, not {spec['model']!r} (answers from different versions would share keys)")
    spec["_dir"], spec["_file"] = path.parent, path
    expand_multi(spec)
    return spec


def expand_multi(spec: dict) -> None:
    """type: multi (several options can apply) → one yes/no question per option, `<qid>__<option>`, like Pydantic
    AI's fan-out of list[Literal]. Each is tested, diffed and reviewed on its own; rows also get `<qid>`, the
    options judged to apply joined by "|", and `test` adds exact-set accuracy. Gold is a "|"-separated set;
    "-" means none apply (an empty cell means no gold)."""
    qs, parents = {}, {}
    for qid, q in (spec.get("questions") or {}).items():
        if q.get("type") != "multi":
            qs[qid] = q
            continue
        crit = q.get("criteria") or {}
        parents[qid] = list(crit)
        for label, desc in crit.items():
            sub = {"type": "noul", "_multi": [qid, label],
                   "instructions": f"{q['instructions']}\nDoes this apply: {label}" + (f" ({desc})" if desc else "") + "?"}
            qs[f"{qid}__{label}"] = sub | {k: q[k] for k in ("act", "gold", "escalate") if k in q}
    if parents:  # `_written` keeps the questions as written, for `hunch docs`
        spec["_written"], spec["questions"], spec["_multi"] = spec["questions"], qs, parents


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
    if texts is None and not path.exists():
        sys.exit(f"{path}: no such file or folder")
    files = [path] if path.is_file() or path.suffix == ".yml" else sorted(path.glob("*.yml"))
    if not files:
        sys.exit(f"{path}: no *.yml specs in this folder")
    if texts is not None:
        files = [path] if path.suffix == ".yml" else [path / name for name in sorted(texts)]
    return topo_project([load_spec(f, texts.get(f.name) if texts is not None else None) for f in files], path)


def topo_project(specs: list[dict], path: Path | None = None) -> dict:
    """Specs (loaded from files, or built in Python) → a project: nodes by name, in dependency order."""
    nodes = {}
    for spec in specs:
        if spec["judgment"] in nodes:
            sys.exit(f"two specs are both named {spec['judgment']!r}; judgment names must be unique")
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
        if ups and "key" in nodes[ups[0]]:  # an upstream without one: lint says it is missing
            spec.setdefault("key", nodes[ups[0]]["key"])
    return {"path": path or specs[0]["_dir"], "nodes": nodes, "order": order}


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


_NAN = object()  # `-cell` of a cell that isn't a number: matches no condition, as the cell itself doesn't


class Unknown(Exception):
    """A where-clause needs an answer that doesn't exist yet (dry runs: compile)."""


def _number(text: str) -> float | None:
    """A cell read as a number, or None when it isn't one ('', 'n/a', 'nan')."""
    try:
        x = float(text)
    except ValueError:
        return None
    return None if x != x else x


def _cell(text: str, other: float) -> float | None:
    """Text compared with a number, read as one; compared with True/False, `true`/`false` (any case) count too."""
    if isinstance(other, bool) and text.strip().lower() in ("true", "false"):
        return text.strip().lower() == "true"
    return _number(text)


def compile_where(expr: str):
    """(predicate, columns used). Columns, constants, comparisons, `in`, and/or/not; nothing else runs."""
    tree = ast.parse(expr, mode="eval")
    for node in ast.walk(tree):
        if not isinstance(node, _ALLOWED):
            raise ValueError(f"{type(node).__name__} not allowed in {expr!r} (use columns, constants, comparisons, and/or/not)")

    def val(node, row):
        if isinstance(node, ast.Constant):
            return node.value
        if isinstance(node, ast.Name):
            if row[node.id] is None:
                raise Unknown(node.id)
            return row[node.id]
        if isinstance(node, (ast.List, ast.Tuple)):
            return [val(e, row) for e in node.elts]
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub):
            x = val(node.operand, row)  # a cell is text: read it as a number
            x = _number(x) if isinstance(x, str) else x
            return _NAN if x is None or x is _NAN else -x
        return ev(node, row)

    def ev(node, row):
        if isinstance(node, ast.BoolOp):
            parts = (ev(v, row) for v in node.values)
            return all(parts) if isinstance(node.op, ast.And) else any(parts)
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.Not):
            return not ev(node.operand, row)
        if isinstance(node, ast.Compare):
            left = val(node.left, row)
            for o, c in zip(node.ops, node.comparators):
                right = val(c, row)
                if not cmp(type(o), left, right):
                    return False
                left = right
            return True
        x = val(node, row)  # a bare column: false when it reads false or 0, or is blank, as `== False` reads it
        return x is not _NAN and bool(x) and not (isinstance(x, str) and (not x.strip() or _cell(x, False) == 0))

    def cmp(o, a, b) -> bool:
        if a is _NAN or b is _NAN:
            return False
        if o in (ast.In, ast.NotIn) and isinstance(b, list):  # item by item, as == and != compare
            return any(cmp(ast.Eq, a, x) for x in b) if o is ast.In else all(cmp(ast.NotEq, a, x) for x in b)
        if o in (ast.In, ast.NotIn) and not (isinstance(a, str) and isinstance(b, str)):
            return False  # substring: text in text only; anything else matches neither `in` nor `not in`
        if o not in (ast.In, ast.NotIn):  # CSV values are text: compare as numbers when one side is
            if isinstance(b, (int, float)) and isinstance(a, str):
                if (a := _cell(a, b)) is None:
                    return False  # an empty or non-numeric cell matches no numeric condition
            elif isinstance(a, (int, float)) and isinstance(b, str):
                if (b := _cell(b, a)) is None:
                    return False
        return _CMP[o](a, b)

    return (lambda row: ev(tree.body, row)), {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}


def bare_names(expr: str) -> set[str]:
    """Columns an expression reads on their own (`flag`, `not flag`, `a and flag`), not compared with anything."""
    try:
        tree = ast.parse(expr, mode="eval")
    except SyntaxError:
        return set()  # lint reports it
    kids = [tree.body, *(v for n in ast.walk(tree) if isinstance(n, ast.BoolOp) for v in n.values),
            *(n.operand for n in ast.walk(tree) if isinstance(n, ast.UnaryOp) and isinstance(n.op, ast.Not))]
    return {k.id for k in kids if isinstance(k, ast.Name)}


def compile_baseline(b) -> tuple:
    """(rule, columns used) for a yes/no question's `baseline`: a rule that answers without a model, scored by `test`
    beside it. A where-expression over columns (yes when it holds), or {match: <regex>, columns: [...]} (yes when
    the regex is found in any of them; an empty cell matches nothing)."""
    if isinstance(b, str):
        pred, used = compile_where(b)

        def rule(row: dict) -> bool:
            try:
                return bool(pred(row))
            except Unknown:  # an upstream answer it reads is missing
                return False
        return rule, used
    cols = b.get("columns") if isinstance(b, dict) else None
    cols = [cols] if isinstance(cols, str) else cols
    if not (isinstance(b, dict) and set(b) == {"match", "columns"} and isinstance(b["match"], str)
            and isinstance(cols, list) and cols and all(isinstance(c, str) and c for c in cols)):
        raise ValueError(f"{b!r} is neither a where-expression nor {{match: <regex>, columns: [<column>, ...]}}")
    rx = re.compile(b["match"])
    return (lambda row: any(row.get(c) not in (None, "") and rx.search(str(row[c])) for c in cols)), set(cols)


def answer_columns(spec: dict) -> list[str]:
    """Columns a judgment adds to each row: label, confidence, p(yes) for yes/no, route when it has act, the
    engine that answered when it can escalate, and a multi question's combined set."""
    cols = list(spec.get("_multi", {}))
    for qid, q in spec.get("questions", {}).items():
        cols += [qid, f"{qid}_p"] + ([f"{qid}_pyes"] if q["type"] == "noul" else []) + ([f"{qid}_route"] if "act" in q else [])
        cols += [f"{qid}_by"] if "escalate" in q else []
    return cols


def api_question(q: dict) -> dict:
    aq = {k: v for k, v in q.items() if k not in HUNCH_ONLY_FIELDS and k != "none"}
    if q.get("none"):  # declining is an answer, not low confidence: an explicit option, sent and keyed
        aq["criteria"] = {**aq["criteria"], NONE: q["none"]}
    return aq


SOURCE_CALL = re.compile(r"^(traces|py)\((.+)\)$")


def source_kind(spec: dict) -> tuple[str, str]:
    """source: a CSV path | traces(<glob>) (agent sessions, see traces.py; `view: turns|runs|commands`) |
    py(<file.py>:<function>) (any function returning dicts: a dlt resource, a query, a generator)."""
    m = SOURCE_CALL.match(str(spec["source"]).strip())
    return (m[1], m[2].strip()) if m else ("csv", str(spec["source"]))


def source_path(spec: dict) -> Path:
    return spec["_dir"] / spec["source"]


def absolute_source(spec: dict) -> str | Path:
    """The same source, independent of the spec's folder (so another spec can read exactly these rows)."""
    kind, arg = source_kind(spec)
    if kind == "csv":
        return source_path(spec).resolve()
    return f"{kind}({(spec['_dir'] / arg).resolve() if not Path(arg).is_absolute() else arg})"


def stringify(r: dict) -> dict:  # rows from code look like CSV rows: text cells, empty for missing
    return {k: "" if v is None else v if isinstance(v, str) else json.dumps(v) if isinstance(v, (dict, list)) else str(v)
            for k, v in r.items()}


def rows(spec: dict) -> list[dict]:
    kind, arg = source_kind(spec)
    if kind == "csv":
        with open(source_path(spec), newline="") as f:
            return list(csv.DictReader(f))
    if kind == "traces":
        from hunch import traces
        return [stringify(r) for r in traces.rows(arg, spec["_dir"], spec.get("view", "turns"))]
    import importlib.util
    file, _, fn = arg.rpartition(":")
    mod_spec = importlib.util.spec_from_file_location(f"hunch_source_{Path(file).stem}", spec["_dir"] / file)
    mod = importlib.util.module_from_spec(mod_spec)
    mod_spec.loader.exec_module(mod)
    return [stringify(dict(r)) for r in getattr(mod, fn)()]


def source_header(spec: dict) -> list[str]:
    kind, _ = source_kind(spec)
    if kind == "csv":
        with open(source_path(spec), newline="") as f:
            if not (header := next(csv.reader(f), None)):
                raise EOFError(f"source {spec['source']} is empty: it needs a header row")
            return header
    if kind == "traces":
        from hunch import traces
        return traces.VIEWS[spec.get("view", "turns")][1]
    return list(dict.fromkeys(k for r in rows(spec)[:100] for k in r))


# ---------- redaction and clipping (applied to state before it is hashed or sent) ----------

REDACTIONS = {
    "secrets": [
        (r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----", "[PRIVATE_KEY]"),
        (r"\b(sk-[A-Za-z0-9_-]{16,}|gh[pousr]_[A-Za-z0-9]{20,}|github_pat_\w{20,}|xox[abpr]-[\w-]{10,}|AKIA[0-9A-Z]{16}"
         r"|AIza[\w-]{30,})", "[SECRET]"),
        (r"(?i)\b(bearer)\s+[\w.~+/-]{16,}=*", r"\1 [SECRET]"),
        (r"(?i)\b([\w-]*(?:api[_-]?key|token|secret|password|passwd)[\w-]*)(['\"]?\s*[=:]\s*['\"]?)[^\s'\",}]{8,}",
         r"\1\2[SECRET]"),  # key=value, key: value, and JSON "key": "value"
        (r"\b[A-Fa-f0-9]{32,}\b|\b[A-Za-z0-9+/_-]{48,}={0,2}", "[BLOB]"),
    ],
    "emails": [(r"\b[\w.+-]+@[\w-]+(?:\.[\w-]+)+\b", "[EMAIL]")],
    "home": [(r"(/Users|/home)/[^/\s]+", "~")],
}


def redaction_rules(spec: dict) -> list[tuple[re.Pattern, str]]:
    """redact: [secrets, emails, home, '<regex>', ...]: named rule sets, or regexes replaced by [REDACTED].
    Idempotent (redacting redacted text changes nothing), so logged traffic replays to the same keys."""
    out = []
    for r in spec.get("redact") or []:
        out += [(re.compile(p, re.S), rep) for p, rep in REDACTIONS[r]] if r in REDACTIONS else [(re.compile(r), "[REDACTED]")]
    return out


def redact(v, rules: list):
    if not isinstance(v, str):
        return v
    for pat, rep in rules:
        v = pat.sub(rep, v)
    return v


def clip(v, n: int):
    """clip: {column: N}: keep the first N characters, or the last -N (the end of a conversation holds its claim)."""
    if not isinstance(v, str) or len(v) <= abs(n):
        return v
    return v[:n] + "…" if n > 0 else "…" + v[n:]


def canon(v):
    """Line endings are transport noise: an app posting a form (\\r\\n) and a batch reading a file (\\n) must share keys.
    Applied to what is sent as well as what is hashed, so one key never stands for two different inputs."""
    return v.replace("\r\n", "\n").replace("\r", "\n") if isinstance(v, str) else v


def state_columns(spec: dict) -> list[str]:
    """`state: [a, b]` sends {"a": …, "b": …}; `state: text` sends that one column bare, as a string, the way a
    Pydantic AI agent sends its prompt."""
    st = spec.get("state")
    return [st] if isinstance(st, str) and st else [c for c in st if isinstance(c, str)] if isinstance(st, list) else []


def state_parts(spec: dict, state) -> dict:
    """A sent state as {column: value}, for showing it to people."""
    return {spec["state"]: state} if isinstance(spec.get("state"), str) else state


def state_of(spec: dict, row: dict) -> dict | str:
    missing = [c for c in state_columns(spec) if c not in row]
    if missing:
        raise KeyError(f"{spec['judgment']}: state needs {missing}")
    rules, clips = redaction_rules(spec), spec.get("clip") or {}
    sent = {col: clip(redact(canon(row[col]), rules), clips[col]) if col in clips else redact(canon(row[col]), rules)
            for col in state_columns(spec)}
    return sent[spec["state"]] if isinstance(spec["state"], str) else sent


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
            "key": digest({"model": spec["model"], "state": state, "question": aq,
                           **({"adapter": LLM_ADAPTER} if is_llm(spec["model"]) else
                              {"adapter": a} if (a := engines.adapter(spec["model"])) else {})})}


def plan(spec: dict, rs: list[dict] | None = None) -> list[dict]:
    return [item(spec, r, qid) for r in (rows(spec) if rs is None else rs) for qid in spec["questions"]]


def label_of(it: dict, width: int = 60, cols: list[str] | None = None) -> str:
    """The item's input as shown to people and to suggest's writer: the state that is sent (redacted, clipped),
    never the raw row."""
    parts = state_parts(it["spec"], it["state"])
    text = " | ".join(str(parts[c]) for c in (cols or list(parts))).replace("\n", " ")
    return text if len(text) <= width else text[: width - 1] + "…"


# ---------- lint ----------

def lint_node(spec: dict, header: list[str] | None) -> tuple[list[str], list[str]]:
    """(errors, warnings) for one judgment, given the columns that reach it. Rules come from API limits and from
    what the prototype measured."""
    errors, warnings = [], []
    for k in {k for k in spec if not k.startswith("_")} - SPEC_KEYS:
        warnings.append(f"unknown spec key {k!r} (typo?)")
    if spec["judgment"] in RESERVED or spec["judgment"].startswith(("_", "sqlite_")):
        errors.append(f"judgment name {spec['judgment']!r} is reserved (the store uses it)")
    st = spec.get("state")
    if not (isinstance(st, str) and st or isinstance(st, list) and all(isinstance(c, str) and c for c in st)):
        errors.append(f"state must be a column name (sent bare, as a string) or a list of them, got {st!r}")
    if spec.get("chain") and "where" not in spec:
        errors.append("chain: true needs a where-clause over an upstream judgment's answers (it is what gets chained)")
    if spec.get("on_change", "reask") not in ON_CHANGE:
        errors.append(f"on_change must be one of {ON_CHANGE}, got {spec['on_change']!r}")
    clip, rs = spec.get("clip") or {}, spec.get("redact") or []
    if not isinstance(clip, dict):
        errors.append(f"clip: maps a state column to N characters ({{command: 1500}}), got {clip!r}")
        clip = {}
    if not (isinstance(rs, list) and all(isinstance(r, str) for r in rs)):
        errors.append(f"redact: lists rule names or regexes ([secrets, 'ghp_…']), got {rs!r}")
        rs = []
    for col, n in clip.items():
        if col not in state_columns(spec):
            errors.append(f"clip: {col!r} is not a state column")
        if not isinstance(n, int) or n == 0:
            errors.append(f"clip: {col}: {n!r} must be a nonzero integer (N keeps the head, -N the tail)")
    for r in rs:
        if r not in REDACTIONS:
            try:
                re.compile(r)
            except re.error as e:
                errors.append(f"redact: {r!r} is neither {sorted(REDACTIONS)} nor a valid regex ({e})")
    if header is not None:
        for col in answer_columns(spec):
            if col in header:
                errors.append(f"answer column {col!r} would overwrite an input column of the same name "
                              f"(e.g. a question named like its gold column); rename the question")
        for col in [spec["key"], *state_columns(spec)]:
            if col not in header:
                errors.append(f"column {col!r} does not reach this judgment (has {header})")
        if "where" in spec:
            try:
                _, used = compile_where(spec["where"])
                for col in sorted(used - set(header)):
                    errors.append(f"where uses {col!r}, which does not reach this judgment")
            except (ValueError, SyntaxError) as e:
                errors.append(f"where: {e}")
    for qid, q in spec["questions"].items():
        for k in set(q) - QUESTION_KEYS:
            warnings.append(f"{qid}: unknown key {k!r} (typo?)")
        if q.get("type") == "noul" and "criteria" in q and (not isinstance(q["criteria"], dict)
                                                            or not {str(k).lower() for k in q["criteria"]} <= {"true", "false"}):
            errors.append(f"{qid}: a noul's criteria are {{\"true\": …, \"false\": …}} (what a yes and a no mean)")
        if "act" in q:
            act = q["act"]
            if isinstance(act, dict):
                if q["type"] != "noul" or set(act) != {"yes", "no"}:
                    errors.append(f"{qid}: act as a mapping must be {{yes: …, no: …}} on a noul question")
                elif not all(isinstance(v, (int, float)) and 0 < v <= 1 for v in act.values()):
                    errors.append(f"{qid}: act thresholds must be in (0, 1], got {act}")
            elif not isinstance(act, (int, float)) or not 0 < act <= 1:
                errors.append(f"{qid}: act must be in (0, 1], got {act}")
        if "none" in q and (q["type"] != "choice" or NONE in (q.get("criteria") or {})):
            errors.append(f"{qid}: none: adds a {NONE!r} option to a choice question (and only once)")
        if "escalate" in q:
            esc = q["escalate"]
            if not isinstance(esc, dict) or "model" not in esc:
                errors.append(f"{qid}: escalate needs {{model: <engine>}}")
            elif "act" not in q:
                errors.append(f"{qid}: escalate re-asks answers below act, so it needs act")
            elif esc["model"] == spec.get("model"):
                errors.append(f"{qid}: escalate.model is the spec's own model")
        if "baseline" in q:
            if q["type"] != "noul":
                errors.append(f"{qid}: baseline is a rule that says yes or no, so it applies to noul questions")
            else:
                try:
                    _, used = compile_baseline(q["baseline"])
                    golds = {g["gold"] for g in spec["questions"].values() if g.get("gold")}
                    for col in sorted(used & golds):
                        errors.append(f"{qid}: baseline reads the gold column {col!r}; it would be scored against itself")
                    for col in sorted(used - set(header) if header is not None else ()):
                        errors.append(f"{qid}: baseline uses {col!r}, which does not reach this judgment")
                except (ValueError, SyntaxError, re.error) as e:
                    errors.append(f"{qid}: baseline: {e}")
        if header is not None and q.get("gold") and q["gold"] not in header:
            # normal for production rows (no gold yet); a warning still catches a typo in the column name
            warnings.append(f"{qid}: gold column {q['gold']!r} does not reach this judgment; these rows have no gold")
        if q["type"] == "choice":
            crit = q.get("criteria") or {}
            if len(crit) > 255:
                errors.append(f"{qid}: {len(crit)} options; the API accepts at most 255")
            bare = [o for o, d in crit.items() if d in (None, "")]
            if 0 < len(bare) < len(crit) and not isinstance(q.get("instructions"), dict):  # dict: recorded from
                # Pydantic AI (spec_from_agent), whose described "none" beside bare options is the agent's own wording
                warnings.append(
                    f"{qid}: {len(crit) - len(bare)} of {len(crit)} options described, {len(bare)} bare "
                    f"(e.g. {', '.join(bare[:4])}). Describe all or none: described options pull answers "
                    f"away from bare neighbours (BANKING77: partial 22 fixed/14 broken, n.s.; full 28/5, p<0.001)")
        if q["type"] == "multi":
            errors.append(f"{qid}: multi questions are expanded when the spec is loaded (internal error)")
        if q["type"] == "score" and not 2 <= len(q.get("criteria") or []) <= 10:
            errors.append(f"{qid}: score needs 2 to 10 levels")
    for qid, q in (spec.get("_written") or {}).items():
        if q.get("type") == "multi" and "baseline" in q:
            errors.append(f"{qid}: baseline applies to noul questions, not multi")
    examples = spec.get("examples") or []
    if not isinstance(examples, list):
        errors.append("examples: a list of {name, row, expect}")
        examples = []
    for i, ex in enumerate(examples, 1):
        where_ = f"examples[{i}]" + (f" ({ex.get('name')})" if isinstance(ex, dict) and ex.get("name") else "")
        if not isinstance(ex, dict) or not isinstance(ex.get("row"), dict) or not isinstance(ex.get("expect"), dict) or not ex["expect"]:
            errors.append(f"{where_}: needs row: {{column: value}} and expect: {{question: answer}}")
            continue
        if ex.get("severity", "error") not in SEVERITIES:
            errors.append(f"{where_}: severity must be one of {SEVERITIES}")
        for k in set(ex) - {"name", "row", "expect", "severity"}:
            warnings.append(f"{where_}: unknown key {k!r} (typo?)")
        for col in state_columns(spec):
            if col not in ex["row"]:
                errors.append(f"{where_}: row needs the state column {col!r}")
        for qid, v in ex["expect"].items():
            if qid in spec.get("_multi", {}):
                errors.append(f"{where_}: {qid} is a multi question; expect each option as {qid}__<option>: yes or no")
                continue
            q = spec["questions"].get(qid)
            if q is None:
                errors.append(f"{where_}: no question {qid!r}")
            elif q["type"] == "noul" and str(v).strip().lower() not in ("yes", "no", "true", "false"):
                errors.append(f"{where_}: {qid} is yes/no, got {v!r}")
            elif q["type"] == "choice" and str(v) not in {*q["criteria"], *([NONE] if q.get("none") else [])}:
                errors.append(f"{where_}: {v!r} is not an option of {qid}")
            elif q["type"] == "score" and next(iter(normalize_gold(q, str(v)))) not in {str(n) for n in range(len(q["criteria"]))}:
                errors.append(f"{where_}: {v!r} is not a level of {qid} (0–{len(q['criteria']) - 1} or a level's text)")
    e, w = lint_rules(spec, header, spec["questions"])
    return errors + e, warnings + w


def lint_rules(spec: dict, header: list[str] | None, questions: dict) -> tuple[list[str], list[str]]:
    """(errors, warnings) for metrics and tests, on a judgment or a union (`questions`: the one it combines)."""
    errors, warnings = [], []
    metrics, tests = (spec.get(k) if spec.get(k) is not None else {} for k in ("metrics", "tests"))
    if not isinstance(metrics, dict):
        errors.append(f"metrics: maps each name to {{rule: <condition>, by: <column>}}, got {metrics!r}")
        metrics = {}
    if not isinstance(tests, dict):
        errors.append(f"tests: maps each question or metric to its checks ({{min_accuracy: 0.9}}), got {tests!r}")
        tests = {}
    if header is not None:
        for name, m in metrics.items():
            if not isinstance(m, dict) or not isinstance(m.get("rule"), str):
                continue  # reported below
            try:
                _, used = compile_where(m["rule"])
                for col in sorted(used - set(header) - set(answer_columns(spec))):
                    errors.append(f"metrics.{name}: rule uses {col!r}, which is neither a column nor an answer of this judgment")
            except (ValueError, SyntaxError) as e:
                errors.append(f"metrics.{name}: {e}")
            if isinstance(m.get("by"), str) and m["by"] not in {*header, *answer_columns(spec)}:
                errors.append(f"metrics.{name}: by {m['by']!r} is neither a column nor an answer of this judgment")
    for name, m in metrics.items():
        if not isinstance(m, dict) or not isinstance(m.get("rule"), str) or set(m) - {"rule", "by"}:
            errors.append(f"metrics.{name}: needs rule: <condition over answers and columns>, and optionally by: <column>")
        elif "by" in m and not (isinstance(m["by"], str) and m["by"]):
            errors.append(f"metrics.{name}: by is one column name, got {m['by']!r}")
        if name in questions or name in spec.get("_multi", {}):
            errors.append(f"metrics.{name}: a question has the same name")
    for qid, conf in tests.items():
        if not isinstance(conf, dict):
            errors.append(f"tests.{qid}: maps each check to its value ({{min_accuracy: 0.9}}), got {conf!r}")
            continue
        if conf.get("severity", "error") not in SEVERITIES:
            errors.append(f"tests.{qid}: severity must be one of {SEVERITIES}")
        for k, v in conf.items():
            if k.startswith(("min_", "max_")) and not (isinstance(v, (int, float)) and 0 <= v <= 1):
                errors.append(f"tests.{qid}.{k}: {v!r} must be a share between 0 and 1 (0.9 for 90%)")
        if qid in metrics:
            for k in set(conf) - METRIC_TEST_KEYS:
                warnings.append(f"tests.{qid}: unknown metric test {k!r} (typo?)")
            if "higher" in conf:
                h = conf["higher"]
                if not isinstance(metrics[qid], dict) or "by" not in metrics[qid]:
                    errors.append(f"tests.{qid}.higher: compares two groups, so the metric needs by: <column>")
                elif not (isinstance(h, list) and len(h) == 2 and all(isinstance(x, (str, int, float)) for x in h)
                          and str(h[0]) != str(h[1])):
                    errors.append(f"tests.{qid}.higher: [group, other group], got {h!r}")
            continue
        if qid in spec.get("_multi", {}):
            if set(conf) - {"min_accuracy", "severity"}:
                warnings.append(f"tests.{qid}: a multi question takes min_accuracy (exact set); per-option tests go "
                                f"under {qid}__<option>")
            continue
        if qid not in questions:
            errors.append(f"tests: no question {qid!r}")
            continue
        for k in set(conf) - TEST_KEYS:
            warnings.append(f"tests.{qid}: unknown test {k!r} (typo?)")
        if "min_recall" in conf and questions[qid]["type"] != "noul":
            errors.append(f"tests.{qid}.min_recall: recall of yes rows applies to noul questions")
        elif "min_recall" in conf and act_needed(questions[qid], "no") is None:
            errors.append(f"tests.{qid}.min_recall: needs `act` (below it a person reads the row; above it a \"no\" is "
                          "set aside unread)")
        if "order_stability" in conf and questions[qid]["type"] != "choice":
            warnings.append(f"tests.{qid}: order_stability only applies to choice questions")
    return errors, warnings


def answer_values(q: dict) -> list[str]:
    """What a question's column holds: the values an app's code compares against (multi: each option, joined by "|")."""
    if not isinstance(q, dict) or q.get("type") == "noul":
        return ["yes", "no"] if isinstance(q, dict) else []
    crit = q.get("criteria")
    labels = [str(c) for c in crit] if isinstance(crit, (list, dict)) else []
    if q.get("type") == "score":
        return [f"{i}:{c}" for i, c in enumerate(labels)]
    return labels + ([NONE] if q.get("none") and q.get("type") == "choice" else [])


def question_values(spec: dict, branch_values: list[dict] = ()) -> dict[str, list[str]]:
    """Every question an exposure can use, with the values its column holds. A union's come from its branches'
    (`branch_values`: each branch's own question_values, so a branch may itself be a union)."""
    if "union" in spec:
        q = spec.get("question")
        return {q: list(dict.fromkeys(v for bv in branch_values for v in bv.get(q, [])))}
    qs = {**(spec.get("questions") or {}), **(spec.get("_written") or {})}  # multi: the parent and one yes/no per option
    return {qid: answer_values(q) for qid, q in qs.items()}


def uses_of(x: dict) -> dict[str, list[str] | None] | None:
    """An exposure's `uses` as {question: the values its code relies on, None for any}; None: it reads every answer."""
    u = x.get("uses")
    if not isinstance(u, dict):
        return None if u is None else dict.fromkeys(u)
    return {q: None if vs is None else [str(v) for v in vs] for q, vs in u.items()}


def uses_text(x: dict) -> str:
    """"reads department = billing, technical; urgent", or "reads every answer"."""
    uses = uses_of(x)
    return "reads " + ("; ".join(f"{q} = {', '.join(vs)}" if vs else q for q, vs in uses.items()) if uses else "every answer")


def relies_on(label: str | None, values: list[str] | None) -> bool:
    """Whether an exposure's code branches on this answer: it relies on any, on this one, or on this score level."""
    level = (label or "").split(":", 1)[0]
    return values is None or (label is not None and (label in values or level.isdigit() and ":" in label and level in values))


def lint_meta(spec: dict, values: dict[str, list[str]]) -> tuple[list[str], list[str]]:
    """description and exposures: for people, `hunch docs` and `hunch diff`, checked on every judgment, unions
    included. An exposure's `uses` can name the answers its code compares against: its contract with this spec."""
    errors, warnings = [], []
    if "description" in spec and not isinstance(spec["description"], str):
        errors.append("description must be text")
    names, exposures = [], spec.get("exposures") or []
    if not isinstance(exposures, list):
        errors.append("exposures must be a list: - name: <what uses this judgment>")
        exposures = []
    for i, x in enumerate(exposures):
        if not isinstance(x, dict) or not isinstance(x.get("name"), str):
            errors.append(f"exposures[{i}]: needs a name (what uses this judgment: an app, hook, dashboard or job)")
            continue
        where_ = f"exposures.{x['name']}"
        names.append(x["name"])
        for k in set(x) - EXPOSURE_KEYS:
            warnings.append(f"{where_}: unknown key {k!r} (typo?)")
        if x.get("kind", "app") not in EXPOSURE_KINDS:
            errors.append(f"{where_}: kind must be one of {EXPOSURE_KINDS}")
        uses = x.get("uses")
        if uses is not None and (not isinstance(uses, (list, dict)) or not uses
                                 or isinstance(uses, list) and not all(isinstance(q, str) for q in uses)
                                 or isinstance(uses, dict) and any(v is not None and (not isinstance(v, list) or not v)
                                                                   for v in uses.values())):
            errors.append(f"{where_}: uses lists the questions it reads ([department]), or maps each to the answers "
                          f"its code compares against (department: [billing, technical]); leave it out if it reads every answer")
            continue
        for qid, relied in (uses_of(x) or {}).items():
            if qid not in values:
                errors.append(f"{where_}: uses {qid!r}, which is not a question here")
                continue
            for v in relied or []:
                if v not in values[qid] and not (v.isdigit() and any(a.startswith(f"{v}:") for a in values[qid])):
                    has = (f"its answers: {', '.join(values[qid])}" if len(values[qid]) <= 10 else f"it has {len(values[qid])}; "
                           f"closest: {', '.join(difflib.get_close_matches(v, values[qid], 3, 0))}")
                    errors.append(f"{where_} relies on {qid} = {v!r}, but {qid} has no such answer ({has}): "
                                  f"if an answer was renamed or removed on purpose, change {x['name']} first, then its uses")
    for n in sorted({n for n in names if names.count(n) > 1}):
        errors.append(f"exposures: {n!r} is listed twice")
    return errors, warnings


def lint_targets(project: dict) -> list[str]:
    """targets: {name: {model, sample, store, max_cost}}. A mistyped key would run a dev target on the production
    engine or store without a word, so it is an error, not a warning. With --target, the name must exist, and what
    applies to the whole run (sample, store, max_cost) must agree between the judgments that set it."""
    errors, used = [], {}
    for n in project["order"]:
        spec, ts = project["nodes"][n], project["nodes"][n].get("targets")
        if ts is None:
            continue
        if not isinstance(ts, dict) or not ts or not all(isinstance(k, str) and isinstance(t, dict) for k, t in ts.items()):
            errors.append(f"{n}: targets maps each name to its settings, e.g. {{dev: {{model: …, sample: 50}}}}")
            continue
        for tn, t in ts.items():
            at = f"{n}: targets.{tn}"
            errors += [f"{at}: unknown key {k!r} (a target sets {', '.join(sorted(TARGET_KEYS))})" for k in sorted(set(t) - TARGET_KEYS)]
            if "model" in t and "union" in spec:
                errors.append(f"{at}.model: a union asks nothing; set it on the judgments it combines")
            elif "model" in t and not (isinstance(t["model"], str) and t["model"]):
                errors.append(f"{at}.model: an engine, e.g. deepseek:deepseek-flash")
            elif "model" in t and (t["model"].endswith("latest") or ":~" in t["model"]):
                errors.append(f"{at}.model: pin an exact version, not {t['model']!r}")
            if "sample" in t and not (type(t["sample"]) is int and t["sample"] > 0):
                errors.append(f"{at}.sample: a number of rows, got {t['sample']!r}")
            if "max_cost" in t and not (type(t["max_cost"]) in (int, float) and t["max_cost"] >= 0):
                errors.append(f"{at}.max_cost: US dollars, got {t['max_cost']!r}")
            if "store" in t and not (isinstance(t["store"], str) and t["store"]):
                errors.append(f"{at}.store: the path of a SQLite file, got {t['store']!r}")
            elif "store" in t:
                t = t | {"store": str((spec["_dir"] / Path(t["store"]).expanduser()).resolve())}
            for k in ("sample", "store", "max_cost"):
                if k in t and isinstance(t[k], (int, float, str)):
                    used.setdefault((tn, k), {}).setdefault(t[k], n)
    name = project.get("target")
    if name and not any(name in (s.get("targets") or {}) for s in project["nodes"].values() if isinstance(s.get("targets"), dict)):
        have = sorted({str(t) for s in project["nodes"].values() if isinstance(s.get("targets"), dict) for t in s["targets"]})
        errors.append(f"--target {name}: no judgment here defines it ({'targets: ' + ', '.join(have) if have else 'no targets:'})")
    for (tn, k), vals in used.items():
        if len(vals) > 1:
            errors.append(f"targets.{tn}.{k} differs between judgments ({', '.join(f'{n}: {v}' for v, n in vals.items())}); "
                          f"one run has one {k}")
    return errors


def apply_target(project: dict, name: str, keep_model: bool = False) -> dict:
    """--target NAME on a linted project: each judgment answers with its targets.NAME.model; a judgment without one
    runs as written. If any engine changes, every table gets `@NAME` (a union or a downstream judgment built on
    target answers must not replace its production table either). Returns what applies to the whole run: sample,
    store (resolved against the spec that names it), max_cost."""
    run, changed = {}, False
    for spec in project["nodes"].values():
        t = (spec.get("targets") or {}).get(name) or {}
        if "model" in t and not keep_model and "union" not in spec and t["model"] != spec.get("model"):
            spec["model"], changed = t["model"], True
        run |= {k: t[k] for k in ("sample", "max_cost") if k in t}
        if "store" in t:
            run["store"] = (spec["_dir"] / Path(t["store"]).expanduser()).resolve()
    for spec in project["nodes"].values() if changed else []:
        spec["_table_suffix"] = f"@{name}"
    return run


def lint(project: dict) -> tuple[list[str], list[str]]:
    """Lint every judgment, tracking which columns flow along each ref() so where-clauses and state are
    checked before anything runs."""
    errors, warnings, columns, vals = [], [], {}, {}
    yes_no = {}  # per judgment: the yes/no answers that reach it (its own, and every upstream's)
    for name in project["order"]:
        spec, ups = project["nodes"][name], upstream(project["nodes"][name])
        tag = lambda xs: [f"{name}: {x}" for x in xs]  # noqa: B023  (used within this iteration only)
        values = vals[name] = question_values(spec, [vals[u] for u in ups if u in vals])
        yes_no[name] = {q for q, vs in values.items() if set(vs) == {"yes", "no"}}.union(*(yes_no.get(u, ()) for u in ups))
        e, w = lint_meta(spec, values)
        errors, warnings = errors + tag(e), warnings + tag(w)
        ms = spec.get("metrics") if isinstance(spec.get("metrics"), dict) else {}  # lint_rules reports any other shape
        rules = {"where": spec.get("where"),
                 **{f"metrics.{k}": m.get("rule") for k, m in ms.items() if isinstance(m, dict)},
                 **{f"{k}: baseline": q.get("baseline") for k, q in (spec.get("questions") or {}).items() if isinstance(q, dict)}}
        for where_, rule in rules.items():  # a yes/no answer is text: 'no' holds on its own
            for col in sorted(bare_names(rule) & yes_no[name]) if isinstance(rule, str) else ():
                warnings += tag([f"{where_}: {col!r} on its own holds for 'no' too; write {col} == 'yes'"])
        if "union" in spec:
            q = spec.get("question")
            qs = {u: asked(project, u, q) for u in ups}  # a branch may itself be a union
            first = next((x for x in qs.values() if x), None)
            for u, x in qs.items():
                if x is None:
                    errors += tag([f"branch {u!r} has no question {q!r}"])
                elif x.get("type") != first.get("type"):
                    errors += tag([f"branch {u!r} asks {q!r} as {x.get('type')}, others differently"])
            known = [columns[u] for u in ups]
            # only what every branch has: a row from a branch without a column has no value for it (KeyError)
            columns[name] = None if None in known else sorted(set.intersection(*map(set, known or [[]])) | {"_branch"})
            if columns[name] is not None and spec.get("key") not in columns[name]:
                errors += tag([f"key {spec.get('key')!r} is not a column every branch has"])
            for k in sorted({k for k in spec if not k.startswith("_")} - UNION_KEYS):
                if k in SPEC_KEYS:
                    errors += tag([f"a union takes no {k!r}: it combines its branches' answers"])
                else:
                    warnings += tag([f"unknown spec key {k!r} (typo?)"])
            e, w = lint_rules(spec, columns[name], {q: first} if first else {})
            errors, warnings = errors + tag(e), warnings + tag(w)
            continue
        missing = [k for k in ("model", "key", "state", "questions") if k not in spec]
        if missing:  # the checks below read them; report once instead of crashing
            errors += tag([f"missing {', '.join(missing)} (every judgment needs model, key, state and questions)"])
            columns[name] = None
            continue
        targets = spec.get("targets") if isinstance(spec.get("targets"), dict) else {}
        for m in [spec["model"], *[q["escalate"]["model"] for q in spec["questions"].values()
                                   if isinstance(q, dict) and isinstance(q.get("escalate"), dict) and "model" in q["escalate"]],
                  *[t["model"] for t in targets.values() if isinstance(t, dict) and isinstance(t.get("model"), str)]]:
            prefix = str(m).split(":", 1)[0] if ":" in str(m) else None
            if prefix and prefix not in engines.RESERVED and prefix not in engines.loaded():
                broken = {p: e for p, e in engines.installed().items() if isinstance(e, Exception)}
                errors += tag([f"model {m!r}: no engine {prefix!r}"
                               + (f" (its plugin failed to load: {broken[prefix]!r})" if prefix in broken else
                                  f". Built in: jev-…, distilled:, {', '.join(p + ':' for p in ENDPOINTS)}; from plugins: "
                                  f"{', '.join(p + ':' for p in engines.loaded()) or 'none installed'}")])
        from hunch import traces
        if source_kind(spec)[0] == "traces" and spec.get("view", "turns") not in traces.VIEWS:
            errors += tag([f"view must be one of {', '.join(traces.VIEWS)}, got {spec['view']!r}"])
            columns[name] = None
            continue
        if ups:
            header = columns[ups[0]]
        else:
            try:
                header = source_header(spec)
            except FileNotFoundError as e:
                errors += tag([f"source not found: {e.filename or e}"])
                header = None
            except EOFError as e:
                errors += tag([str(e)])
                header = None
        e, w = lint_node(spec, header)
        errors, warnings = errors + tag(e), warnings + tag(w)
        if "weights" in spec:
            wt = spec["weights"]
            if ups:
                errors += tag(["weights describe how source rows were sampled; set them on the judgment that reads the file"])
            elif not isinstance(wt, dict):
                errors += tag([f"weights needs by: <column> and population: {{<value>: <share>, ...}}, got {wt!r}"])
            elif header is not None and wt.get("by") not in header:
                errors += tag([f"weights.by column {wt.get('by')!r} not in the source"])
            elif not isinstance(wt.get("population"), dict) or not wt["population"]:
                errors += tag([f"weights.population maps each value of by to its share ({{x: 0.2, y: 0.8}}), "
                               f"got {wt.get('population')!r}"])
            elif any(isinstance(v, bool) or not isinstance(v, (int, float)) or not v > 0 for v in wt["population"].values()):
                errors += tag([f"weights.population shares must be numbers above 0, got {wt.get('population')} (a row "
                               "whose share is 0 stands for no one: leave it out of the source)"])
            elif abs(sum(wt["population"].values()) - 1) > 0.01:
                errors += tag([f"weights.population shares must sum to 1, got {wt.get('population')}"])
        # an unreadable source makes every column downstream unknown (not missing): no cascade of false errors
        columns[name] = None if header is None else header + answer_columns(spec) + ["_path_p"] + (["_w"] if "weights" in spec else [])
    errors += lint_targets(project)
    if len(project["nodes"]) == 1:  # a single spec: no need to name it in every message
        name = project["order"][0]
        errors = [x.removeprefix(f"{name}: ") for x in errors]
        warnings = [x.removeprefix(f"{name}: ") for x in warnings]
    warnings += [f"engine plugin {x} ignored: its prefix is built in" for x in engines.shadowed]
    return errors, warnings


# ---------- store ----------

_conns: dict[tuple[Path, int], sqlite3.Connection] = {}


def store_path(start: Path) -> Path:
    if settings.STORE is not None:
        return settings.STORE
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
    One connection per thread and store; writes are short explicit transactions.
    One store per workspace: $HUNCH_STORE, else the nearest `.hunch/store.sqlite` in this folder or above,
    else a new one here. Answers are content-addressed facts; a store per folder made comparisons across
    projects pay twice."""
    path = store_path(spec["_dir"])
    if (path, threading.get_ident()) in _conns:  # one connection per thread: WAL lets them share the file
        return _conns[(path, threading.get_ident())]
    if not path.exists() and settings.VERBOSE:  # the command summary shows the chosen store; verbose explains why it is new
        print(f"new answer store: {path} (HUNCH_STORE=... to share one)", file=sys.stderr)
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
    _conns[(path, threading.get_ident())] = db
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

# ---------- engines ----------
# A spec's `model` picks the engine: "jev-…" is TypeSafe's System One; "<endpoint>:<id>" is an LLM read through
# its first answer token's log-probabilities. Both return the same answer shapes, so everything downstream
# (routing, tests, diff, review) is engine-neutral, and the model is part of every cache key.

OPENROUTER = "https://openrouter.ai/api/v1"
ENDPOINTS = {  # OpenAI-compatible chat APIs that return top logprobs
    "openrouter": {"base": OPENROUTER, "key": "OPENROUTER_API_KEY"},  # prices from its model listing
    "deepseek": {"base": "https://api.deepseek.com", "key": "DEEPSEEK_API_KEY", "body": {"thinking": {"type": "disabled"}},
                 "price": (0.15e-6, 0.60e-6)},  # list price per token (in, out); cache hits and off-peak cost less
}
engines.RESERVED |= set(ENDPOINTS) | {"distilled"}
LLM_ADAPTER = "logprobs-v1"  # part of an LLM answer's key: temperature 1, top-20, numbered options, question first
LLM_OVERHEAD_TOKENS = 40  # prompt scaffolding per request beyond ~chars/4
LLM_MAX_TOKENS = 256  # reply allowance per question: room for providers that reason briefly anyway
_llm_prices: dict[str, tuple[float, float]] = {}
_reasoning: dict[str, dict] = {}  # per model: reasoning off where allowed (it hides logprobs), else minimal


def is_llm(model: str) -> bool:
    return model.split(":", 1)[0] in ENDPOINTS


def endpoint(model: str) -> dict:
    return ENDPOINTS[model.split(":", 1)[0]]


def llm_prices(model: str) -> tuple[float, float]:
    """(input, output) price per token: the endpoint's list price or, on OpenRouter, the dearest provider's for
    that model. OpenRouter's model listing shows the cheapest, and a pinned or fallback provider can charge 3x it,
    so the cap's worst case takes the most any of them charges. Spend is always the provider's reported cost."""
    if "price" in endpoint(model):
        return endpoint(model)["price"]
    mid = llm_route(model)[0]
    if mid not in _llm_prices:
        eps = (httpx.get(f"{OPENROUTER}/models/{mid}/endpoints", timeout=30).json().get("data") or {}).get("endpoints")
        if not eps:
            raise SystemExit(f"openrouter: no model {mid!r} with a live endpoint (see openrouter.ai/models)")
        _llm_prices[mid] = (max(float(e["pricing"]["prompt"]) for e in eps),
                            max(float(e["pricing"].get("completion") or 0) for e in eps))
    return _llm_prices[mid]


def price_per_token(model: str) -> float:
    """Input price; estimates ignore an LLM's few output tokens."""
    return llm_prices(model)[0] if is_llm(model) else PRICE_PER_INPUT_TOKEN


def llm_route(model: str) -> tuple[str, dict]:
    """"<endpoint>:<id>[@provider]" → (id, OpenRouter provider preferences). Pin a provider for reproducible
    answers: providers serve different builds of one model, and some ignore "reasoning off"."""
    mid, _, provider = model.split(":", 1)[1].partition("@")
    pref = {"require_parameters": True, **({"only": [provider]} if provider else {"sort": "price"})}
    return mid, pref


def estimate_cost(model: str, groups: list[list[dict]]) -> float:
    if model.startswith("distilled:"):
        return 0.0
    if engines.get(model):  # a plugin says only its worst case: estimate with that
        return sum(worst_cost(model, g) for g in groups)
    if not is_llm(model):
        return estimate_tokens(groups) * PRICE_PER_INPUT_TOKEN
    tokens = sum((len(json.dumps(it["state"])) + len(json.dumps(it["aq"]))) / 4 + LLM_OVERHEAD_TOKENS
                 for g in groups for it in g)  # one request per question
    return tokens * price_per_token(model)


def _bytes(x) -> int:
    return len(json.dumps(x, ensure_ascii=False).encode())


def worst_cost(model: str, g: list[dict]) -> float:
    """The most one request (one row's questions) can be charged, so --max-cost holds on the charged cost, not
    the estimate. Tokenizers spend at most one token per byte: every Jev request in the store (100,000+, 12 specs)
    was charged at most bytes + 184 tokens, under the 275-token overhead allowed here. An LLM may use its whole
    reply allowance."""
    if model.startswith("distilled:"):
        return 0.0
    if e := engines.get(model):
        return e.worst_cost(model, g[0]["state"], {it["rid"]: it["aq"] for it in g}) if hasattr(e, "worst_cost") else 0.0
    if not is_llm(model):
        return (_bytes(g[0]["state"]) + sum(_bytes(it["aq"]) for it in g) + REQUEST_OVERHEAD_TOKENS) * PRICE_PER_INPUT_TOKEN
    p_in, p_out = llm_prices(model)
    return sum((len(llm_prompt(it["aq"], g[0]["state"])[0].encode()) + LLM_OVERHEAD_TOKENS) * p_in
               + LLM_MAX_TOKENS * p_out for it in g)


def llm_prompt(aq: dict, state: dict) -> tuple[str, list[str], list[str]]:
    """(prompt, answer codes, labels). The fixed part (question, options) comes first so providers can cache it
    across rows; the row's input comes last. Options are numbered: one short token each, whatever the label."""
    t = aq["type"]
    if t == "choice":
        labels = list(aq["criteria"])
        opts = "\n".join(f"{i}. {k}: {v}" if v else f"{i}. {k}" for i, (k, v) in enumerate(aq["criteria"].items(), 1))
        codes, tail = [str(i) for i in range(1, len(labels) + 1)], "Answer with only the number of the best option."
    elif t == "noul":
        labels = codes = ["yes", "no"]
        crit = {str(k).lower(): v for k, v in (aq.get("criteria") or {}).items()}
        opts = "\n".join(f"{w.capitalize()} means: {crit[k]}" for w, k in (("yes", "true"), ("no", "false")) if crit.get(k))
        tail = "Answer with only yes or no."
    elif t == "score":
        labels = list(aq["criteria"])
        opts = "\n".join(f"{i}. {k}" for i, k in enumerate(labels))
        codes, tail = [str(i) for i in range(len(labels))], "Answer with only the number of the level that fits best."
    else:
        raise ValueError(f"engine openrouter: unsupported question type {t!r}")
    q = aq["instructions"] if isinstance(aq["instructions"], str) else json.dumps(aq["instructions"], ensure_ascii=False)
    prompt = (f"Question: {q}\n" + (f"\nOptions:\n{opts}\n" if opts else "")
              + f"\n{tail}\n\nInput (JSON):\n{json.dumps(state, ensure_ascii=False)}")
    return prompt, codes, labels


def llm_answer(aq: dict, codes: list[str], labels: list[str], logprobs: list[dict]) -> dict:
    """Probabilities over the options from the first token that is an answer code, renormalised over codes
    (top-20 logprobs: options outside the top 20 get 0). Same shapes as Jev's answers."""
    pos = next((x for x in logprobs if any(y["token"].strip().lower() in codes for y in x["top_logprobs"])), None)
    if pos is None:
        raise RuntimeError(f"no answer code among the output tokens {[x['token'] for x in logprobs[:5]]}")
    mass: dict[str, float] = {}
    for x in pos["top_logprobs"]:
        c = x["token"].strip().lower()
        if c in codes:
            mass[c] = mass.get(c, 0.0) + math.exp(x["logprob"])
    tot = sum(mass.values())
    probs = {lab: round(mass.get(c, 0.0) / tot, 4) for c, lab in zip(codes, labels)}
    if aq["type"] == "noul":
        return {"type": "noul", "noul": probs["yes"]}
    best = max(probs, key=probs.get)
    if aq["type"] == "choice":
        return {"type": "choice", "choice": best, "confidence": probs[best], "probabilities": probs}
    legend = {str(i): lab for i, lab in enumerate(labels)}
    return {"type": "score", "score": round(sum(i * probs[lab] for i, lab in enumerate(labels)), 4),
            "confidence": probs[best], "legend": legend,
            "probabilities": {str(i): probs[lab] for i, lab in enumerate(labels)}}


async def post(client: httpx.AsyncClient, sem, url: str, body: dict) -> dict:
    """POST with retries on rate limits, overload and transport errors. A request that was billed but whose reply
    was lost is billed again by its retry, and only the reply that arrives is counted: rare, and not in the cap."""
    last = "no response"
    async with sem:
        for attempt in range(8):
            try:
                r = await client.post(url, json=body)
            except httpx.TransportError as e:
                last = repr(e)
                settings.RETRIES["transport"] += 1
                await asyncio.sleep(0.5 * 2**attempt)
                continue
            if r.status_code in (429, 529) or r.status_code >= 500:
                last = f"HTTP {r.status_code}"
                settings.RETRIES[r.status_code] += 1
                await asyncio.sleep(min(float(r.headers.get("retry-after") or 0.5 * 2**attempt), 30))
                continue
            if r.status_code >= 400:
                raise RuntimeError(f"HTTP {r.status_code}: {r.text[:300]}")
            j = r.json()
            if "error" in j:  # OpenRouter reports some provider failures inside a 200
                last = str(j["error"])[:200]
                await asyncio.sleep(0.5 * 2**attempt)
                continue
            return j
    raise RuntimeError(f"gave up after retries ({last})")


async def ask(client: httpx.AsyncClient, sem, model: str, state: dict, questions: dict) -> dict:
    """{answers: {rid: answer}, tokens, cost, model} for one row's questions."""
    if e := engines.get(model):
        async with sem:
            got = await engines.call(e, model, state, questions)
        return {"answers": engines.check(model, questions, got), "tokens": int(got.get("tokens") or 0),
                "cost": float(got.get("cost") or 0.0), "model": model}
    if not is_llm(model):
        j = await post(client, sem, API, {"model": model, "state": state, "questions": questions})
        tokens = j["usage"]["input_tokens"]
        return {"answers": j["answers"], "tokens": tokens, "cost": tokens * PRICE_PER_INPUT_TOKEN, "model": j["model"]}

    async def one(rid: str, aq: dict) -> tuple[str, dict, dict]:
        prompt, codes, labels = llm_prompt(aq, state)
        mid, pref = llm_route(model)
        ep = endpoint(model)
        body = {"model": mid, "messages": [{"role": "user", "content": prompt}],
                "max_tokens": LLM_MAX_TOKENS,  # the answer's logprobs come after any brief reasoning
                # temperature 1 = the model's own distribution; at 0 some APIs return -9999 for every other token.
                # Only the probabilities are read, never the sampled text.
                "temperature": 1, "logprobs": True, "top_logprobs": 20, **ep.get("body", {})}
        if ep["base"] == OPENROUTER:
            body |= {"reasoning": _reasoning.get(model, {"effort": "none"}), "provider": pref}
        try:
            j = await post(client, sem, f"{ep['base']}/chat/completions", body)
        except RuntimeError as e:
            minimal = {"effort": "minimal"}  # measured on GLM: 0 reasoning tokens, logprobs kept
            if "mandatory" not in str(e) or body["reasoning"] == minimal:
                raise
            _reasoning[model] = body["reasoning"] = minimal
            j = await post(client, sem, f"{ep['base']}/chat/completions", body)
        c = j["choices"][0]
        lp = (c.get("logprobs") or {}).get("content") or []
        try:
            u = j["usage"]
            if "cost" not in u:
                pin, pout = ep["price"]
                u["cost"] = u["prompt_tokens"] * pin + u.get("completion_tokens", 0) * pout
            return rid, llm_answer(aq, codes, labels, lp), u
        except RuntimeError as e:
            raise RuntimeError(f"{e}; provider {j.get('provider')}, reply {c['message'].get('content')!r}, "
                               f"finish {c.get('finish_reason')} (this provider may reason despite 'off'; pin one with "
                               f"openrouter:<id>@<provider>)") from None

    got = await asyncio.gather(*[one(rid, aq) for rid, aq in questions.items()])
    return {"answers": {rid: a for rid, a, _ in got}, "tokens": sum(u["prompt_tokens"] for *_, u in got),
            "cost": sum(u.get("cost", 0.0) for *_, u in got), "model": model}


async def fill(spec: dict, db, items: list[dict]) -> tuple[dict, dict]:
    """Ensure every item has an answer. Missing questions of the same row share one request (read once).
    Each response is saved as it arrives, so a failure part-way loses nothing already paid for."""
    have = cached(db, list({it["key"] for it in items}))
    missing: dict[str, dict[str, dict]] = {}
    for it in items:
        if it["key"] not in have:
            # one request per distinct state (not per row id: two rows can share an id and differ in text)
            missing.setdefault(it["shash"], {})[it["key"]] = it  # dedupe identical keys within a request
    group = [list(g.values()) for g in missing.values()]
    model = spec["model"]
    stats = {"cached": sum(it["key"] in have for it in items), "asked": 0, "tokens": 0, "cost": 0.0,
             "requests": sum(len(g) for g in group) if is_llm(model) else len(group)}

    if group and model.startswith("distilled:"):  # a local student (hunch distill): no key, no cost, no network
        from hunch import distill
        got = distill.answer(spec, model, [it for g in group for it in g])
        write(db, "insert or replace into answers (key, model, answer, input_tokens) values (?, ?, ?, 0)",
              [[k, model, json.dumps(a)] for k, a in got.items()])
        have.update(got)
        stats["asked"] += len(got)
        return have, stats
    if group:
        for w in oversized([it for g in group for it in g])[:5]:
            print(f"  size warning: {w}", file=sys.stderr)
        est = estimate_cost(model, group)
        cap = settings.MAX_COST  # this fill's cap: --max-cost, and what is left of a total across fills
        if settings.SPEND_LIMIT is not None:
            cap = max(0.0, settings.SPEND_LIMIT - settings.CHARGED) if cap is None else min(cap, max(0.0, settings.SPEND_LIMIT - settings.CHARGED))
        if cap is not None and est > cap:
            raise SystemExit(f"{spec.get('judgment', '')}: would ask {sum(map(len, group))} answers in {len(group)} requests "
                             f"(~${est:.4f}), above --max-cost ${cap:.4g}; nothing asked")
        print(f"  asking {sum(map(len, group))} answers in {stats['requests']} requests (~${est:.4f})", file=sys.stderr)
        plugin = engines.get(model)
        var = getattr(plugin, "key", None) if plugin else endpoint(model)["key"] if is_llm(model) else "TYPESAFE_API_KEY"
        key = (os.environ.get(var) if var else "none needed") or (None if is_llm(model) or plugin else os.environ.get("TYPESAFE_AI_API_KEY"))
        if not key:
            raise SystemExit(f"{spec.get('judgment', '')}: set {var} to ask {model} ({len(group)} requests to send)")
        # The estimate can be low (chars/4 undercounts dense text: 20% on BANKING77, 41% on shell commands), so the
        # cap is also kept while asking: a request is sent only if the charged cost so far, plus the worst case of
        # every request in flight and of this one, stays within it. Asyncio runs one task at a time, so the check
        # and the reservation can't interleave.
        held, stop, over = [0.0], [0], []
        async with httpx.AsyncClient(headers={"Authorization": f"Bearer {key}"}, timeout=120) as client:
            n = getattr(plugin, "concurrency", None) or CONCURRENCY
            sem, gate = asyncio.Semaphore(n), asyncio.Semaphore(n)

            async def one(g: list[dict]) -> None:
                async with gate:
                    worst = worst_cost(model, g)
                    if cap is not None and (stop[0] or stats["cost"] + held[0] + worst > cap):
                        stop[0] += 1
                        return
                    held[0] += worst
                    try:
                        res = await ask(client, sem, model, g[0]["state"], {it["rid"]: it["aq"] for it in g})
                    finally:
                        held[0] -= worst
                    if plugin and cap is not None and res["cost"] > worst + 1e-12:  # its worst case was wrong
                        over.append((res["cost"], worst))
                        stop[0] += 1
                tokens = res["tokens"]
                write(db, "insert or replace into answers (key, model, answer, input_tokens) values (?, ?, ?, ?)",
                      [[it["key"], res["model"], json.dumps(res["answers"][it["rid"]]), tokens / len(g)] for it in g])
                stats["tokens"] += tokens
                stats["cost"] += res["cost"]
                settings.CHARGED += res["cost"]
                stats["asked"] += len(g)
                for it in g:
                    have[it["key"]] = res["answers"][it["rid"]]

            t0, r0 = time.perf_counter(), dict(settings.RETRIES)
            results = await asyncio.gather(*[one(g) for g in group], return_exceptions=True)
            dt = time.perf_counter() - t0
            retried = {k: v - r0.get(k, 0) for k, v in settings.RETRIES.items() if v - r0.get(k, 0)}
            if settings.VERBOSE or retried:
                print(f"  {stats['requests']} requests in {dt:.1f}s ({stats['requests'] / dt:.1f}/s at concurrency {n})"
                      + (f"; retried {retried}" if retried else ""), file=sys.stderr)
        failed = [r for r in results if isinstance(r, BaseException)]
        broke = next((r for r in failed if isinstance(r, engines.ContractError)), None)
        if broke:  # a plugin's bug: retrying won't fix it
            raise SystemExit(f"{spec.get('judgment', '')}: {broke} ({stats['asked']} answers saved)")
        if over and cap is not None:
            raise SystemExit(f"{spec.get('judgment', '')}: engine {model.split(':', 1)[0]!r} charged ${over[0][0]:.6f} for "
                             f"one request, above the ${over[0][1]:.6f} its worst_cost allows, so --max-cost can't "
                             f"hold; stopped after ${stats['cost']:.4f} ({stats['asked']} answers saved)")
        if failed:
            raise RuntimeError(f"{len(failed)}/{len(group)} requests failed; {stats['asked']} answers saved, "
                               f"re-run to retry only the rest. First error: {failed[0]}") from failed[0]
        if stop[0]:
            raise SystemExit(f"{spec.get('judgment', '')}: stopped at --max-cost ${cap:.4g}: ${stats['cost']:.4f} charged for "
                             f"{stats['asked']} answers (saved); {stop[0]} requests not sent, as the next could have "
                             f"gone over. Re-run with a higher --max-cost to ask only the rest")
    return have, stats


def merge_stats(*stats: dict) -> dict:
    return {k: sum(s[k] for s in stats) for k in ("cached", "asked", "requests", "tokens", "cost")}


STATE_LIMIT_TOKENS = 32_000  # Jev: state + the longest question must fit in 32k tokens (64k per request)
WARN_AT = 0.8


def oversized(items: list[dict]) -> list[str]:
    """Rows near the engine's input limit, with their largest column: past it the request fails, and clipping
    (spec `clip:`) is the fix."""
    out, seen = [], set()
    for it in items:
        if it["id"] in seen:
            continue
        seen.add(it["id"])
        tokens = (len(json.dumps(it["state"])) + len(json.dumps(it["aq"]))) / 4
        if tokens > WARN_AT * STATE_LIMIT_TOKENS:
            parts = state_parts(it["spec"], it["state"])
            big = max(parts, key=lambda c: len(str(parts[c])))
            out.append(f"row {it['id']}: ~{tokens:,.0f} tokens (limit {STATE_LIMIT_TOKENS:,}); largest column {big!r} "
                       f"~{len(str(parts[big])) / 4:,.0f} → clip: {{{big}: N}}")
    return out


def estimate_tokens(groups: list[list[dict]]) -> float:
    """Input tokens for these requests (one per group of items sharing a row): ~chars/4 plus a measured fixed
    overhead per request. An estimate: 3% high on the command guard recipe, 20% low on BANKING77 (77 long
    options), 41% low on real shell commands; --max-cost is kept on the charged cost (worst_cost)."""
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


async def aexecute(project: dict, rows_in: list[dict] | None = None, dry: bool = False, hold: bool = False) -> dict[str, dict]:
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
            if dupes and not dry:  # a dry run keeps rows with unknown answers in every branch (upper bound)
                sys.exit(f"{name}: rows {dupes[:5]} reach more than one branch; a union needs branches whose where-clauses don't overlap")
            results[name] = {"spec": spec, "rows": [r | {"_branch": u} for u, res in zip(ups, parts) for r in res["rows"]],
                             "items": items, "answers": {k: v for res in parts for k, v in res["answers"].items()},
                             "stats": dict(ZERO), "input": sum(len(res["rows"]) for res in parts), "unknown": 0, "hits": set()}
            continue
        inp = results[ups[0]]["rows"] if ups else (rows_in if rows_in is not None else rows(spec))
        if not ups and "weights" in spec and rows_in is None:
            inp = weigh(inp, spec["weights"])
        if not ups and rows_in is None and settings.SAMPLE is not None:
            inp = sorted(inp, key=lambda r: hashlib.sha256(str(r.get(spec["key"], "")).encode()).hexdigest())[:settings.SAMPLE]
        keep, unknown, known, passed, unknown_rows = inp, 0, 0, 0, []
        if "where" in spec:
            pred, _ = compile_where(spec["where"])
            keep = []
            for r in inp:
                try:
                    ok = pred(r)
                    known, passed = known + 1, passed + ok
                except Unknown:  # only a dry run has missing answers: keep the row (upper bound)
                    ok, unknown = dry, unknown + dry
                    unknown_rows.append(r)
                if ok:
                    keep.append(r)
            if inp and not keep and rows_in is None:  # a batch keeping nothing is likely a typo or a type mismatch
                print(f"{name}: warning: where kept 0 of {len(inp)} rows: {spec['where']}", file=sys.stderr)
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
        if rows_in is None and not ups:
            dup = [i for i, c in Counter(str(r.get(spec["key"])) for r in keep).items() if c > 1]
            if dup:
                print(f"{name}: warning: {len(dup)} key values repeat (e.g. {dup[:3]}); rows are paired by key in "
                      f"diff, review and on_change, so make `{spec['key']}` unique", file=sys.stderr)
        items = plan(spec, keep)
        if hold:
            hold_answers(spec, open_store(spec), items)
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
        answers, by, stats = await escalate(spec, db, items, answers, stats, dry, hits)
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
                if "escalate" in it["q"]:
                    out[f"{qid}_by"] = by.get(it["key"], spec["model"]) if a else None
            for parent, labels in spec.get("_multi", {}).items():
                out[parent] = "|".join(lab for lab in labels if out.get(f"{parent}__{lab}") == "yes")
            out_rows.append(out)
        # rows the where-clause is expected to keep, from the pass rate of rows whose answers are known
        expected = passed + unknown * (passed / known) if known else None
        results[name] = {"spec": spec, "rows": out_rows, "items": items, "answers": answers, "stats": stats,
                         "input": len(inp), "unknown": unknown, "hits": hits, "escalated": by,
                         "expected": expected if unknown else None, "pass_rate": passed / known if known else None,
                         "unknown_ids": {str(r.get(spec.get("key"))) for r in unknown_rows}}
    return results


async def escalate(spec: dict, db, items: list[dict], answers: dict, stats: dict, dry: bool,
                   hits: set) -> tuple[dict, dict, dict]:
    """escalate: {model: X} on a question re-asks, on engine X, only the answers that fall below `act`; the
    escalated answer replaces the original when it clears `act` itself. Behind a distilled student it always does:
    the teacher is the better model, and a student's confidence is not comparable with its teacher's. Both stay in the store; the returned
    answers are the combined system's, `by` says which key was answered by which engine. Escalated answers that
    were already stored are added to `hits`."""
    todo: dict[str, list[dict]] = {}
    for it in items:
        a = answers.get(it["key"])
        if "escalate" in it["q"] and a and route(it["q"], a, it.get("path_p", 1.0)) == "review":
            todo.setdefault(it["q"]["escalate"]["model"], []).append(it)
    if not todo:
        return answers, {}, stats
    final, by = dict(answers), {}
    for model, its in todo.items():
        espec = {**spec, "model": model}
        eitems = [item(espec, it["row"], it["qid"]) for it in its]
        hits |= set(cached(db, [e["key"] for e in eitems]))
        if dry:
            got, estats = cached(db, [e["key"] for e in eitems]), {**ZERO}
        else:
            got, estats = await fill(espec, db, eitems)
        stats = merge_stats(stats, estats)
        for it, e in zip(its, eitems):
            ea = got.get(e["key"])
            if ea and (route(it["q"], ea, it.get("path_p", 1.0)) == "act" or spec["model"].startswith("distilled:")):
                it["key"] = e["key"]  # the row's answer is now the escalated one (lineage, drift and tests follow)
                final[e["key"]], by[e["key"]] = ea, model
    return final, by, stats


def hold_answers(spec: dict, db, items: list[dict]) -> None:
    """on_change: new_rows_only: after a spec edit, a row answered before keeps the answer it got, as long as its
    input is unchanged (same id and same state; edited text is a new row); only new rows are asked. Applied by
    `run` and `compile` (what run will cost); test, diff, review and judge always see the spec as written.
    (freeze is checked before a run; reask, the default, asks every row under the new spec.) Rewrites keys."""
    if spec.get("on_change", "reask") != "new_rows_only":
        return set()
    prev = previous_keys(db, table_name(spec))
    for it in items:
        old = prev.get((it["id"], it["qid"]))
        if old and old[1] == it["shash"]:
            it["key"] = old[0]


def previous_keys(db, judgment: str) -> dict[tuple[str, str], tuple[str, str]]:
    row_answers_table(db)
    return {(r, q): (k, s) for r, q, k, s in db.execute(  # run ids start with their time: the last one wins
        "select row_id, qid, key, shash from _hunch_row_answers where judgment = ? order by run_id", (judgment,))}


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


def asked(project: dict, name: str, qid: str) -> dict | None:
    """The question `qid` as judgment `name` asks it; a union's, as its first branch that asks it (a branch may
    itself be a union, which carries only its own `question`)."""
    spec = project["nodes"].get(name, {})
    if "union" not in spec:
        return (spec.get("questions") or {}).get(qid)
    if spec.get("question") != qid:
        return None
    return next((q for u in upstream(spec) if (q := asked(project, u, qid))), None)


# ---------- commands ----------

def cmd_lint(project: dict, _args) -> None:
    print(f"lint: ok ({len(project['nodes'])} judgment{'s' if len(project['nodes']) > 1 else ''})")  # main() printed findings


def cmd_compile(project: dict, args) -> None:
    results = execute(project, dry=True, hold=True)  # what `run` would ask, on_change included
    for n in frozen_changes(project):
        print(f"# {n}: on_change: freeze and the spec changed since the last complete run: `run` will refuse "
              f"without --allow-change")
    first = next(n for n in project["order"] if "union" not in project["nodes"][n])
    name = args.node or first
    its = results[name]["items"]
    if its:
        row_items = [it for it in its if it["id"] == its[0]["id"]]
        model = row_items[0]["spec"]["model"]
        if is_llm(model):
            print(f"# {name}: prompt for row {its[0]['id']}, question {row_items[0]['qid']} (one request per question)")
            print(llm_prompt(row_items[0]["aq"], row_items[0]["state"])[0])
        else:
            payload = {"model": model, "state": row_items[0]["state"], "questions": {it["qid"]: it["aq"] for it in row_items}}
            print(f"# {name}: request for row {its[0]['id']} (one request per row, all questions read once"
                  + (f"; engine {model.split(':', 1)[0]!r} is a plugin and asks in its own way)" if engines.get(model) else ")"))
            print(json.dumps(payload, indent=2, ensure_ascii=False))
    total, expected_total = 0.0, 0.0
    print()
    for n in project["order"]:
        res, spec = results[n], project["nodes"][n]
        if "union" in spec:
            print(f"# {n}: union of {', '.join(spec['union'])} ({len(res['rows'])} rows)")
            continue
        todo = [it for it in res["items"] if it["key"] not in res["hits"]]
        big = oversized(res["items"])
        for w in big[:5]:
            print(f"# {n}: size warning: {w}")
        if len(big) > 5:
            print(f"# {n}: … {len(big) - 5} more rows near the limit")
        groups: dict[str, list[dict]] = {}
        for it in todo:
            groups.setdefault(it["id"], []).append(it)
        cost = estimate_cost(spec["model"], list(groups.values()))
        est = cost / price_per_token(spec["model"])
        if res.get("unknown"):  # rows kept only because their where-clause can't be decided yet
            unk = res["unknown_ids"]
            sure = estimate_cost(spec["model"], [g for i, g in groups.items() if i not in unk])
            maybe = cost - sure
            rate = res.get("pass_rate")
            expected_cost = sure + maybe * rate if rate is not None else None
            expected_total = None if expected_total is None or expected_cost is None else expected_total + expected_cost
        elif expected_total is not None:
            expected_total += cost
        total += cost
        bound = "≤ " if res["unknown"] else ""
        where = f", where keeps {bound}{len(res['rows'])}" if "where" in spec else ""
        print(f"# {n}: {res['input']} rows in{where}; {len(res['items'])} answers planned, {len(res['items']) - len(todo)} cached, "
              f"{bound}{len(todo)} to ask, ~{est:,.0f} input tokens, ~${cost:.5f} ({spec['model']})")
        if res["unknown"]:
            exp = res.get("expected")
            print(f"#   {res['unknown']} rows kept because the answers their where-clause needs aren't cached yet: "
                  + (f"expected ~{exp:.0f} rows, ~${expected_cost:.5f} (from the {res['pass_rate']:.0%} pass rate of "
                     f"rows already answered)" if exp is not None
                     else f"an upper bound; `hunch run --node {upstream(spec)[0]}` first for the real count"))
    if len(project["nodes"]) > 1:
        print(f"# total: ~${total:.5f}" + (f" upper bound, ~${expected_total:.5f} expected" if expected_total is not None
                                              and expected_total < total else ""))


def output_columns(project: dict, results: dict, name: str) -> list[str]:
    """A judgment's output columns: from its rows, or, when it kept none, from its input and questions."""
    if results[name]["rows"]:
        return list(dict.fromkeys(c for r in results[name]["rows"] for c in r))
    spec, ups = project["nodes"][name], upstream(project["nodes"][name])
    if "union" in spec:
        return list(dict.fromkeys([c for u in ups for c in output_columns(project, results, u)] + ["_branch"]))
    inp = output_columns(project, results, ups[0]) if ups else source_header(spec) + (["_w"] if "weights" in spec else [])
    multi = list(spec.get("_multi", {}))  # rows put a multi question's combined set after its parts
    return list(dict.fromkeys(inp + ["_path_p"] + [c for c in answer_columns(spec) if c not in multi] + multi))


def materialize(spec: dict, db, out_rows: list[dict], run_id: str = "", keys: dict | None = None,
                columns: list[str] | None = None) -> None:
    """The judgment's table: its input columns plus its answers, like a dbt model's select *, plus lineage:
    `_hunch_run_id` and, per question, `<qid>_key` (the content address of the answer in the store).
    `columns` are used when there are no rows, so a filter that keeps nothing still leaves a (empty) table.
    Replaced in one transaction, so a reader sees the previous complete run or this one, never a mix."""
    lineage = []
    if keys is not None:
        out_rows = [{**r, "_hunch_run_id": run_id, **keys.get(i, {})} for i, r in enumerate(out_rows)]
        lineage = ["_hunch_run_id", *(f"{q}_key" for q in spec["questions"])]
    cols = list(dict.fromkeys(c for r in out_rows for c in r)) or [*(columns or []), *lineage]
    typed = ", ".join(f'"{c}" {"real" if c.endswith(("_p", "_pyes")) else "text"}' for c in cols)
    table = table_name(spec)
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


def record_run(db, run: dict) -> None:
    db.execute("""create table if not exists _hunch_runs (run_id text, judgment text, spec_hash text, git_sha text,
        model text, rows integer, asked integer, cost real, status text, started_at text, finished_at text)""")
    write(db, "insert into _hunch_runs values (:run_id, :judgment, :spec_hash, :git_sha, :model, :rows, :asked, :cost, "
              ":status, :started_at, :finished_at)", [run])


def row_answers_table(db) -> None:
    """One entry per row, question and run: the history drift checks compare; on_change reads each row's latest.
    Stores from before `shash` existed get the column (their old rows can't be matched, so they are re-asked)."""
    db.execute("""create table if not exists _hunch_row_answers (judgment text, row_id text, qid text, key text,
        shash text, run_id text, primary key (judgment, row_id, qid, run_id))""")
    if "shash" not in {r[1] for r in db.execute("pragma table_info(_hunch_row_answers)")}:
        db.execute("alter table _hunch_row_answers add column shash text")


def record_row_answers(db, judgment: str, items: list[dict], run_id: str) -> None:
    row_answers_table(db)
    write(db, "insert or replace into _hunch_row_answers (judgment, row_id, qid, key, shash, run_id) "
              "values (?, ?, ?, ?, ?, ?)", [(judgment, it["id"], it["qid"], it["key"], it["shash"], run_id) for it in items])


def git_sha(path: Path) -> str:
    try:
        return subprocess.run(["git", "-C", str(path), "rev-parse", "--short", "HEAD"], capture_output=True,
                              text=True, timeout=5).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return ""


def frozen_changes(project: dict) -> list[str]:
    """on_change: freeze: judgments whose spec differs from their last complete run."""
    out = []
    for n in project["order"]:
        spec = project["nodes"][n]
        if spec.get("on_change") != "freeze":
            continue
        try:
            last = open_store(spec).execute("select spec_hash from _hunch_runs where judgment = ? and status = 'complete' "
                                            "order by finished_at desc limit 1", (table_name(spec),)).fetchone()
        except sqlite3.OperationalError:
            last = None
        if last and last[0] != spec_hash(spec):
            out.append(n)
    return out


def with_upstream(project: dict, name: str) -> dict:
    """The part of a project that `name` needs: it and every judgment it reads from (dbt's +model)."""
    need, todo = set(), [name]
    while todo:
        n = todo.pop()
        if n not in need:
            need.add(n)
            todo += upstream(project["nodes"][n])
    return {**project, "nodes": {n: s for n, s in project["nodes"].items() if n in need},
            "order": [n for n in project["order"] if n in need]}


def cmd_run(project: dict, args) -> None:
    from hunch import presentation

    if getattr(args, "node", None):
        project = with_upstream(project, pick(project, args.node))
    changed = frozen_changes(project)
    if changed and not getattr(args, "allow_change", False):
        sys.exit(f"on_change: freeze, and {changed} changed since the last complete run: nothing asked. "
                 f"`hunch diff` shows what the change does; `hunch run --allow-change` accepts it")
    started = time.strftime("%Y-%m-%dT%H:%M:%S")
    run_id = f"{started}-{digest([started, os.getpid(), time.time_ns()])[:6]}"
    def failed(names: list[str], e: BaseException) -> None:  # recorded too; their tables keep the last complete run
        for n in names:
            spec = project["nodes"][n]
            record_run(open_store(spec), {"run_id": run_id, "judgment": table_name(spec), "spec_hash": spec_hash(spec),
                                          "git_sha": git_sha(spec["_dir"]), "model": spec.get("model", ""), "rows": 0,
                                          "asked": 0, "cost": 0.0, "status": f"failed: {e!r}"[:200],
                                          "started_at": started, "finished_at": time.strftime("%Y-%m-%dT%H:%M:%S")})

    try:
        results = execute(project, hold=True)
    except BaseException as e:
        failed(project["order"], e)
        raise
    if settings.SAMPLE is not None:  # a sample must never replace a table that downstream readers take for the whole
        presentation.run(project, args, results, presentation.relative(store_path(project["nodes"][project["order"][0]]["_dir"])), None,
                         sample=settings.SAMPLE)
        return
    for i_node, n in enumerate(project["order"]):
        res, spec = results[n], project["nodes"][n]
        db = open_store(spec)
        nq = max(1, len(question_of(spec))) if "union" not in spec else 1
        keys = None
        try:  # lineage first, the table last: a failure here leaves the previous table in place
            if "union" not in spec:
                keys = {}
                for i, it in enumerate(res["items"]):
                    keys.setdefault(i // nq, {})[f"{it['qid']}_key"] = it["key"]
                record_row_answers(db, table_name(spec), [it for it in res["items"] if it["key"] in res["answers"]], run_id)
            materialize(spec, db, res["rows"], run_id, keys, output_columns(project, results, n))
        except BaseException as e:
            failed(project["order"][i_node:], e)
            raise
        record_run(db, {"run_id": run_id, "judgment": table_name(spec), "spec_hash": spec_hash(spec), "git_sha": git_sha(spec["_dir"]),
                        "model": spec.get("model", ""), "rows": len(res["rows"]), "asked": res["stats"]["asked"],
                        "cost": round(res["stats"]["cost"], 6), "status": "complete", "started_at": started,
                        "finished_at": time.strftime("%Y-%m-%dT%H:%M:%S")})
        if "union" in spec:
            detail(f"{n}: union of {len(spec['union'])} judgments → table \"{n}{spec.get('_table_suffix', '')}\" ({len(res['rows'])} rows)")
            continue
        where = f", where kept {len(res['rows'])}" if "where" in spec else ""
        if settings.VERBOSE:
            print_stats(res["stats"], f"{n}: {res['input']} rows in{where}; answers")
        for qid, q in spec["questions"].items():
            if "act" in q:
                k = sum(1 for r in res["rows"] if r.get(f"{qid}_route") == "review")
                detail(f"  {qid}: {k} rows below act={q['act']} → review queue")
    if len(project["nodes"]) > 1 and settings.VERBOSE:
        print_stats(merge_stats(*(r["stats"] for r in results.values())), "total")
    where = store_path(project["nodes"][project["order"][0]]["_dir"])
    presentation.run(project, args, results, presentation.relative(where), run_id, sample=None)


def table_name(spec: dict) -> str:
    """The judgment's identity in the store: its name, plus the engine when run with --model."""
    return spec["judgment"] + spec.get("_table_suffix", "")


def spec_hash(spec: dict) -> str:
    """What the judgment asks: not its policy (on_change) or where this run's rows come from (source, which
    --source and --traffic replace) or how other targets would run it, so freeze guards the questions, not the data."""
    return digest({k: v for k, v in spec.items() if not k.startswith("_") and k not in ("on_change", "source", "targets", *META_KEYS)})[:12]


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


def _wmean(reviewed: list[dict], answers: dict, w) -> tuple[float, float, float]:
    """Weighted share of hits among reviewed rows, with a Wilson interval on the effective sample size
    (Kish: (Σw)² / Σw²). Unit weights give k/n and wilson(k, n) exactly."""
    ws = [w(it) for it in reviewed]
    sw = sum(ws)
    p = sum(wi for wi, it in zip(ws, reviewed) if hit(it, answers[it["key"]])) / sw
    n_eff = sw * sw / sum(wi * wi for wi in ws)
    lo, hi = wilson(p * n_eff, n_eff)
    return p, lo, hi


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
        p, lo, hi = _wmean(audits, answers, w)
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
        p, l, h = _wmean(reviewed, answers, w)
        l, h = (p, p) if len(reviewed) >= len(members) else (l, h)
        share = sum(w(it) for it in members) / total
        est, lo, hi = est + share * p, lo + share * l, hi + share * h
    return est, lo, hi, detail


class Checks:
    """Collects PASS/FAIL lines so `test` can exit non-zero, and each check as data for results.json."""
    failed = False

    def __init__(self) -> None:
        self.log: list[dict] = []
        self.severity = "error"  # set by the caller for a group of checks (a question's, a metric's, an example's)

    def __call__(self, ok: bool, text: str, check: str = "", value: float | None = None, limit: float | None = None) -> None:
        warn = not ok and self.severity == "warn"
        self.failed |= not ok and not warn
        self.log.append({"check": check, "passed": ok, "value": _r(value), "limit": limit, "severity": self.severity})
        detail(f"  {'PASS' if ok else 'WARN' if warn else 'FAIL'} {text}")


def assessment(report: dict) -> str:
    """What a completed test established, separately from its process exit status."""
    parts = [*report.get("questions", {}).values(), *report.get("multi", {}).values(),
             *report.get("metrics", {}).values()]
    checks = [c for part in parts for c in part.get("checks", [])]
    checks += [{"passed": e["passed"], "severity": e.get("severity", "error")}
               for e in report.get("examples", [])]
    failed = [c for c in checks if not c["passed"]]
    if any(c.get("severity", "error") == "error" for c in failed):
        return "failed"
    if failed:
        return "warning"
    if any(part.get("unavailable_checks") for part in parts):
        return "unassessed"
    if checks:
        return "passed"
    measured = any(q.get("accuracy") for q in report.get("questions", {}).values())
    measured |= any(q.get("exact_set_accuracy") is not None for q in report.get("multi", {}).values())
    measured |= any(m.get("rate") is not None for m in report.get("metrics", {}).values())
    return "measured" if measured else "no_gold"


def overall_assessment(report: dict) -> str:
    states = {r["assessment"] for r in report.values()}
    return next((s for s in ("failed", "warning", "unassessed", "passed", "measured", "no_gold") if s in states),
                "no_gold")


def _r(x: float | None) -> float | None:
    return None if x is None else round(float(x), 4)


def print_dial(q: dict, rows_: list[tuple]) -> list[dict]:
    """rows_: (answer, correct, weight, path confidence). Share of rows acted on automatically at each threshold,
    and how many of those are wrong. Yes/no questions get one column per side: the two sides are separate decisions."""
    total = sum(r[2] for r in rows_)

    def side(label: str | None, t: float) -> tuple[float, float]:
        auto = [(c, w) for a, c, w, pp in rows_ if decide(a)[1] * pp >= t and (label is None or decide(a)[0] == label)]
        ws = sum(w for _, w in auto)
        return ws / total, (sum(w for c, w in auto if not c) / ws if ws else 0.0)

    dial = []
    if q["type"] == "noul":
        detail("       dial   act on yes: automated  wrong   │  act on no: automated  wrong")
        for t in DIAL:
            (ya, yw), (na, nw) = side("yes", t), side("no", t)
            mark = "".join(f"  ← act {s}" for s in ("yes", "no") if act_needed(q, s) == t)
            detail(f"       {t:>4.2f}   {ya:>20.1%}  {yw:>5.1%}   │  {na:>19.1%}  {nw:>5.1%}{mark}")
            dial.append({"threshold": t, "yes": {"automated": _r(ya), "wrong": _r(yw)}, "no": {"automated": _r(na), "wrong": _r(nw)}})
    else:
        detail("       dial   automated   wrong among automated")
        for t in DIAL:
            auto, wrong = side(None, t)
            detail(f"       {t:>4.2f}   {auto:>9.1%}   {wrong:>21.1%}{'  ← act' if act_needed(q, '') == t else ''}")
            dial.append({"threshold": t, "automated": _r(auto), "wrong": _r(wrong)})
    return dial


def it_spec_chain(its: list[dict]) -> bool:
    return any(it["spec"].get("chain") for it in its)


def test_question(spec: dict, qid: str, q: dict, its: list[dict], answers: dict, check: "Checks", all_stats: list) -> dict:
    """Prints the question's report and returns it as data (results.json)."""
    conf = (spec.get("tests") or {}).get(qid, {})
    detail(f"\n{qid} ({q['type']}, {len(its)} rows)")
    src = Counter(it["gold_src"] for it in its)
    gold_its = [it for it in its if it["gold"]]
    out: dict = {"type": q["type"], "rows": len(its), "gold": {"rows": len(gold_its), "source": src["source"],
                                                               "review": src["review"], "excluded": src["excluded"]}}
    first_check = len(check.log)
    check.severity = conf.get("severity", "error")
    if "act" in q and its:  # at the spec's own threshold: the share of all rows acted on, and how often those are wrong
        acted = [it for it in its if route(q, answers[it["key"]], it.get("path_p", 1.0)) == "act"]
        judged = [it for it in acted if it["gold"]]
        out["act"] = {"threshold": q["act"], "automated": _r(len(acted) / len(its)), "judged": len(judged),
                      "wrong": _r(sum(not hit(it, answers[it["key"]]) for it in judged) / len(judged)) if judged else None}
    if not gold_its:
        configured = {k for k in conf if k in TEST_KEYS and k != "severity"}
        if "order_stability" in configured and "max_flip_rate" not in conf["order_stability"]:
            configured.remove("order_stability")
        return out | {"checks": [], "unavailable_checks": sorted(configured)}
    both = sum(len(it["gold"]) > 1 for it in gold_its)
    detail(f"  gold: {len(gold_its)} rows ({src['source']} from source, {src['review']} from review"
          f"{f', {both} with two acceptable labels' if both else ''}"
          f"{f', {src['excluded']} excluded as ambiguous or needing more context' if src['excluded'] else ''})")
    spot = [it for it in its if it["review_kind"] == "audit"]
    out["spot_checks"] = len(spot)
    if gap := sum(it["verdict"] == "needs_context" for it in spot):
        out["needs_context"] = gap
        detail(f"  context: {gap} of {len(spot)} random spot checks ({gap / len(spot):.0%}) needed more than the state "
              f"shows to decide; give the state more (earlier or later turns, what happened next)")
    reviewed = src["review"] + src["excluded"] > 0 and any(it["raw_gold"] for it in its)  # raw vs reviewed needs a key

    weights, note = None, ""
    if any("_w" in it["row"] for it in gold_its):
        weights = {it["id"]: it["row"]["_w"] for it in gold_its}
        tw = sum(weights.values())
        yes = sum(weights[it["id"]] for it in gold_its if "yes" in it["gold"]) / tw if q["type"] == "noul" else None
        note = " [weighted to the population]"
        detail("  weighted to the population (source sampling weights)"
              + (f": {yes:.0%} yes among these rows, {sum('yes' in it['gold'] for it in gold_its) / len(gold_its):.0%} in the sample" if yes is not None else ""))
    out["weighted"] = bool(weights)
    w = (lambda it: weights[it["id"]]) if weights else (lambda it: 1.0)

    acc = sum(w(it) * hit(it, answers[it["key"]]) for it in gold_its) / sum(w(it) for it in gold_its)
    est = estimate_accuracy(its, answers, {it["id"]: it["row"]["_w"] for it in its} if weights else None)
    if not isinstance(est, str):
        # Headline = the estimate. Accuracy on "current gold" trusts every unreviewed row, so once
        # disagreements are corrected it only errs upward.
        e, lo, hi, d = est
        out["accuracy"] = {"value": _r(e), "ci": [_r(lo), _r(hi)], "basis": "estimate",
                           "reviewed": {g: {"reviewed": n, "of": m} for g, (n, m) in d.items()}}
        msg = f"estimated accuracy {e:.1%} (95% CI {lo:.1%}–{hi:.1%}) from reviews of " + ", ".join(
            f"{n}/{m} {g if g == 'random' else g + 'ing'} rows" for g, (n, m) in d.items())
        if "min_accuracy" in conf:
            want = conf["min_accuracy"]
            check(e >= want, f"{msg} (min {want:.0%})", "min_accuracy", e, want)
        else:
            detail(f"  {msg}")
        if "random" not in d:
            detail(f"       not the headline: on current gold {acc:.1%} (trusts unreviewed rows), "
                  f"on the raw answer key {accuracy(its, answers, qid, 'raw_gold'):.1%}")
    else:
        raw = f" (raw source gold: {accuracy(its, answers, qid, 'raw_gold'):.1%})" if reviewed else ""
        out["accuracy"] = {"value": _r(acc), "ci": None, "basis": "gold"}
        if "min_accuracy" in conf:
            want = conf["min_accuracy"]
            check(acc >= want, f"accuracy {acc:.1%}{raw}{note} (min {want:.0%})", "min_accuracy", acc, want)
        else:
            detail(f"  accuracy {acc:.1%}{raw}{note}")
        if reviewed:
            detail(f"       upper bound only, no estimate: {est}")

    ece, table = calibration(calib_pairs(its, answers, weights=weights))
    raw = f" (raw source gold: {calibration(calib_pairs(its, answers, 'raw_gold', weights))[0]:.3f})" if reviewed else ""
    out["calibration_error"] = _r(ece)
    if "max_calibration_error" in conf:
        want = conf["max_calibration_error"]
        check(ece <= want, f"calibration error {ece:.3f}{raw}{note} (max {want})", "max_calibration_error", ece, want)
    else:
        detail(f"  calibration error {ece:.3f}{raw}{note}")
    detail(f"         {'stated p(yes)' if q['type'] == 'noul' else 'stated p':<13} {'n':>5}   avg stated   observed")
    for lo_, hi_, n, c, a in table:
        detail(f"       {lo_:.1f}–{hi_:.1f}      {n:>6}   {c:>10.3f}   {a:>8.3f}")

    if q["type"] == "noul":
        pos = [answers[it["key"]]["noul"] for it in gold_its if "yes" in it["gold"]]
        neg = [answers[it["key"]]["noul"] for it in gold_its if "no" in it["gold"]]
        if pos and neg:
            auc = auroc(pos, neg)
            out["auroc"] = _r(auc)
            if "min_auroc" in conf:
                want = conf["min_auroc"]
                check(auc >= want,
                      f"AUROC {auc:.3f} ({len(pos)} yes / {len(neg)} no; 0.5 = coin toss; unaffected by base rate) (min {want})",
                      "min_auroc", auc, want)
            else:
                detail(f"  AUROC {auc:.3f} ({len(pos)} yes / {len(neg)} no)")

    offered = [it for it in gold_its if it["aq"].get("type") == "choice" and isinstance(it["aq"].get("criteria"), dict)]
    lost = [it for it in offered if not it["gold"] & set(it["aq"]["criteria"])]
    if lost:
        detail(f"       {len(lost)} of {len(offered)} rows' gold is not among the options this judgment offered "
              f"(sent to the wrong place upstream; no answer here could be right)")
    if it_spec_chain(its):
        own = [(decide(answers[it["key"]])[1], hit(it, answers[it["key"]])) for it in gold_its]
        chn = [(conf_of(it, answers[it["key"]]), h) for it, (_, h) in zip(gold_its, own)]
        sep = lambda xs: auroc([c for c, h in xs if h], [c for c, h in xs if not h]) if 0 < sum(h for _, h in xs) < len(xs) else float("nan")
        detail(f"       confidence is chained (× P(routed here correctly)); it separates right from wrong answers "
              f"with AUROC {sep(chn):.3f}, own confidence alone {sep(own):.3f}")
    scored = [(answers[it["key"]], hit(it, answers[it["key"]]), w(it), it.get("path_p", 1.0)) for it in gold_its]
    out["dial"] = print_dial(q, scored)
    if "act" in q and "min_act_accuracy" in conf:
        acted = [(c, wt) for (a, c, wt, pp) in scored if route(q, a, pp) == "act"]
        ws = sum(wt for _, wt in acted)
        a_acc = sum(wt for c, wt in acted if c) / ws if ws else 1.0
        check(a_acc >= conf["min_act_accuracy"],
              f"accuracy among auto-acted {a_acc:.1%} on {ws / sum(sc[2] for sc in scored):.0%} of rows at act={q['act']} (min {conf['min_act_accuracy']:.0%})",
              "min_act_accuracy", a_acc, conf["min_act_accuracy"])

    if q["type"] == "noul" and any("yes" in it["gold"] for it in gold_its):
        out["recall"] = test_recall(q, conf, its, gold_its, answers, w, check)

    if q["type"] == "noul" and "baseline" in q and len({json.dumps(it["q"].get("baseline")) for it in its}) == 1:
        # a union reports it only when every branch has the same rule; each branch is tested with its own
        out["baseline"] = test_baseline(q, gold_its, answers, w, out.get("auroc"))

    wrong = sorted((it for it in gold_its if not hit(it, answers[it["key"]])), key=lambda it: -conf_of(it, answers[it["key"]]))
    detail(f"       most confident mistakes ({len(wrong)} total; high confidence + wrong = dangerous, or a gold error → hunch review):")
    out["mistakes"] = {"total": len(wrong), "most_confident": [
        {"id": it["id"], "got": decide(answers[it["key"]])[0], "p": _r(conf_of(it, answers[it["key"]])), "gold": sorted(it["gold"])}
        for it in wrong[:SHOW]]}
    for it in wrong[:SHOW]:
        got = f"{decide(answers[it['key']])[0]} {conf_of(it, answers[it['key']]):.2f}"
        detail(f"         #{it['id']:>4} gold={gold_str(it['gold']):<32} got {got:<38} {label_of(it, 50)}")
    detail("       most confused (gold → got):")
    for (g, got), n in Counter((gold_str(it["gold"]), decide(answers[it["key"]])[0]) for it in wrong).most_common(8):
        detail(f"         {n:>3}  {g} → {got}")

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
            ba = answers.get(item(b["spec"], b["row"], qid)["key"]) or answers[b["key"]]  # the base engine's own answer,
            # not an escalated one: permutations are asked of the base engine, so that is what they must match
            (bl, bp, bm), (vl, _, vm) = decide(ba), decide(vans[v["key"]])
            dp.append(abs(bp - vans[v["key"]]["probabilities"][bl]))
            if bl != vl:
                flips.append((b, v))
                noisy += min(bm, vm) < NOISE
        rate = len(flips) / len(variants)
        msg = (f"order stability: {len(flips)}/{len(variants)} answers flip ({rate:.1%}, {noisy} within noise band), "
               f"mean |Δp| of original answer {sum(dp) / len(dp):.3f}")
        if "max_flip_rate" in order:
            check(rate <= order["max_flip_rate"], f"{msg} (max flip rate {order['max_flip_rate']:.0%})",
                  "order_stability", rate, order["max_flip_rate"])
        else:
            detail(f"  {msg}")
        for b, v in flips[:SHOW]:
            detail(f"         #{b['id']:>4} {v['rid']:<14} {show(answers.get(item(b['spec'], b['row'], qid)['key']) or answers[b['key']]):<36} → {show(vans[v['key']]):<36} {label_of(b, 40)}")
    out["checks"] = check.log[first_check:]
    configured = {k for k in conf if k in TEST_KEYS and k != "severity"}
    if "order_stability" in configured and "max_flip_rate" not in conf["order_stability"]:
        configured.remove("order_stability")
    out["unavailable_checks"] = sorted(configured - {c["check"] for c in out["checks"]})
    return out


def test_recall(q: dict, conf: dict, its: list[dict], gold_its: list[dict], answers: dict, w, check: "Checks") -> dict:
    """Recall of yes: a person reads every row except the "no" answers confident enough to act on, which are set
    aside unread; recall is the share of gold-yes rows a person still sees (weighted, with a Wilson interval on
    the effective sample size, as accuracy). `min_recall` checks the interval's lower bound at the spec's own `act`,
    because a review that must not miss a rare yes (papers for a systematic review, privileged documents) needs the
    worst plausible recall, not the point estimate. Also reported: the lowest bar for "no" that still meets it, and
    the share of rows a person then reads."""
    no_p = {it["id"]: (p * it.get("path_p", 1.0) if label == "no" else -1.0)  # confidence of a "no", else -1
            for it in its for label, p, _ in [decide(answers[it["key"]])]}
    yes = [it for it in gold_its if "yes" in it["gold"]]
    ws = [w(it) for it in yes]
    n_eff = sum(ws) ** 2 / sum(x * x for x in ws)
    rw = [it["row"].get("_w", 1.0) for it in its]  # every row, gold or not: what a person reads

    def at(bar: float) -> tuple[float, float, float, float]:  # recall, its interval, share of rows read
        r = sum(wi for wi, it in zip(ws, yes) if no_p[it["id"]] < bar) / sum(ws)
        lo, hi = wilson(r * n_eff, n_eff)
        return r, lo, hi, sum(x for x, it in zip(rw, its) if no_p[it["id"]] < bar) / sum(rw)

    out: dict = {"yes_rows": len(yes)}
    want = conf.get("min_recall")
    need = act_needed(q, "no")
    if need is not None:
        r, lo, hi, read = at(need)
        out["at_act"] = {"threshold": need, "value": _r(r), "ci": [_r(lo), _r(hi)], "read": _r(read)}
        msg = (f"recall of yes {r:.1%} (95% CI {lo:.1%}–{hi:.1%}) of {len(yes)} gold-yes rows at act {need}: a person "
               f"reads {read:.0%} of rows, the confident \"no\" answers are set aside")
        if want is not None:
            check(lo >= want, msg + f" (min {want:.0%} on the interval's lower bound)", "min_recall", lo, want)
        else:
            detail(f"  {msg}")
    if want is not None:  # the lowest bar (most rows set aside) whose worst plausible recall still meets it
        ok = next((o for b in sorted({round(v, 3) for v in no_p.values() if v >= 0.5} | {1.001})
                   if (o := (b, *at(b)))[2] >= want), None)
        if ok and ok[0] <= 1:
            b, r, lo, hi, read = ok
            out["lowest_act"] = {"threshold": b, "value": _r(r), "ci": [_r(lo), _r(hi)], "read": _r(read)}
            detail(f"       lowest act for no that keeps recall ≥ {want:.0%} (lower bound): {b} → recall {r:.1%} "
                  f"({lo:.1%}–{hi:.1%}), a person reads {read:.0%} of rows")
        else:
            detail(f"       no bar keeps recall ≥ {want:.0%} on the lower bound: {len(yes)} gold-yes rows"
                  f"{f' (effective {n_eff:.0f} after weights)' if abs(n_eff - len(yes)) > 0.5 else ''} can't show it; "
                  "review more yes rows, or read everything")
    return out


def test_baseline(q: dict, gold_its: list[dict], answers: dict, w, model_auc: float | None) -> dict:
    """The question's `baseline` rule scored on the same gold rows as the model, weighted the same way, so a question
    that loses to a keyword search says so. The model's side is its own label (yes at p(yes) ≥ 0.5); AUROC is the
    model's p(yes) against the rule's yes/no (ties count half)."""
    rule, _ = compile_baseline(q["baseline"])

    def score(says_yes: list[bool]) -> dict:
        def share(pairs: list) -> float | None:
            tw = sum(w(it) for it, _ in pairs)
            return _r(sum(w(it) for it, ok in pairs if ok) / tw) if tw else None
        both = list(zip(gold_its, says_yes))
        return {"accuracy": share([(it, ("yes" if y else "no") in it["gold"]) for it, y in both]),
                "recall": share([(it, y) for it, y in both if "yes" in it["gold"]]),
                "precision": share([(it, "yes" in it["gold"]) for it, y in both if y])}

    said = [rule(it["row"]) for it in gold_its]
    pos = [float(y) for it, y in zip(gold_its, said) if "yes" in it["gold"]]
    neg = [float(y) for it, y in zip(gold_its, said) if "no" in it["gold"]]
    b = score(said) | {"auroc": _r(auroc(pos, neg)) if pos and neg else None}
    m = score([decide(answers[it["key"]])[0] == "yes" for it in gold_its]) | {"auroc": _r(model_auc)}
    ahead = m["accuracy"] > b["accuracy"]
    bl = q["baseline"]
    shown = bl if isinstance(bl, str) else f"/{bl['match']}/ in " + ", ".join([bl["columns"]] if isinstance(bl["columns"], str) else bl["columns"])
    fmt = lambda k, v: "–" if v is None else f"{v:.3f}" if k == "auroc" else f"{v:.1%}"
    detail(f"  baseline: {shown}\n       says yes on {sum(said)} of {len(said)} gold rows")
    for k in ("accuracy", "auroc", "recall", "precision"):
        detail(f"       {k + (' of yes' if k == 'recall' else ''):<14} rule {fmt(k, b[k]):>7}   model {fmt(k, m[k]):>7}")
    if not ahead:
        detail(f"       the rule is as accurate as the model or more ({fmt('accuracy', b['accuracy'])} vs "
              f"{fmt('accuracy', m['accuracy'])}): the question has to beat it to earn its cost")
    return {"rule": q["baseline"], "rows": len(said), "said_yes": sum(said), **b, "model": m, "model_ahead": ahead}


def test_metric(spec: dict, name: str, rule: str, res: dict, check: "Checks") -> dict:
    """A rule over a row, counted twice: on the model's answers (every row: exact), and on gold (with a 95% interval),
    and the two compared on the same rows: what the rule missed and its false alarms. Gold comes from every row when
    every row has it for the questions the rule reads, else from random spot checks only (rows reviewed for any other
    reason would bias it). A question's gold stands in for its answer: the label, `_p` 1.0, `_pyes` 1 or 0; when two
    labels are acceptable, the model's if it is one of them."""
    conf = (spec.get("tests") or {}).get(name, {})
    pred, used = compile_where(rule)
    qs = question_of(spec)  # a union has one question, and one item per row
    qids = [q for q in qs if {q, f"{q}_p", f"{q}_pyes"} & used]
    nq = len(qs)
    rows = []  # (fired on answers, gold row items or None)
    for i, r in enumerate(res["rows"]):
        try:
            fired = bool(pred(r))
        except Unknown:  # an answer the rule needs is missing
            continue
        rows.append((r, fired, {it["qid"]: it for it in res["items"][i * nq:(i + 1) * nq]}))
    k = sum(f for _, f, _ in rows)
    weighted = any(r.get("_w", 1.0) != 1.0 for r, _, _ in rows)  # then every line is, as the groups are
    detail(f"\n{name} (metric: {rule})")
    if weighted:
        detail("  weighted to the population (source sampling weights)")
    out: dict = {"rule": rule, "rows": len(rows), "fired": k, "rate": None}
    p = 0.0
    if not rows:
        detail("  on answers: no rows")
    elif weighted:  # the rows sample the population: the rate is an estimate, with an interval
        p, lo, hi, n_eff = wrate([(f, r.get("_w", 1.0)) for r, f, _ in rows])
        detail(f"  on answers: {k} of {len(rows)} rows ({p:.1%}, 95% CI {lo:.1%}–{hi:.1%}, effective n {n_eff:.0f})")
        out |= {"rate": _r(p), "ci": [_r(lo), _r(hi)], "effective_rows": _r(n_eff)}
    else:
        p = k / len(rows)
        detail(f"  on answers: {k} of {len(rows)} rows ({p:.1%})")
        out["rate"] = _r(p)
    first_check = len(check.log)
    check.severity = conf.get("severity", "error")
    if "min_rate" in conf:
        check(p >= conf["min_rate"], f"rate {p:.1%} (min {conf['min_rate']:.1%})", "min_rate", p, conf["min_rate"])
    if "max_rate" in conf:
        check(p <= conf["max_rate"], f"rate {p:.1%} (max {conf['max_rate']:.1%})", "max_rate", p, conf["max_rate"])
    if qids:
        census = all(its[q]["gold"] for _, _, its in rows for q in qids)
        pairs = []
        for r, fired, its in rows:
            if not all(its[q]["gold"] and (census or its[q]["review_kind"] == "audit") for q in qids):
                continue
            g = dict(r)
            for q in qids:
                gold = its[q]["gold"]
                label = r[q] if r[q] in gold else min(gold)
                g[q], g[f"{q}_p"] = label, 1.0
                if its[q]["q"]["type"] == "noul":
                    g[f"{q}_pyes"] = 1.0 if label == "yes" else 0.0
            pairs.append((fired, bool(pred(g)), r))
        basis = f"all {len(pairs)} rows with gold" if census else f"{len(pairs)} random spot checks"
        out["gold"] = {"basis": "census" if census else "spot checks", "rows": len(pairs)}
        if pairs:
            def rate_line(label: str, key: str, fw: list[tuple[bool, float]], of: str) -> float:
                hits, n = sum(f for f, _ in fw), len(fw)
                if not n:
                    detail(f"  {label}: none of {of}")
                    out[key] = {"count": 0, "of": 0, "rate": None, "ci": None}
                    return 0.0
                p, lo, hi, n_eff = wrate(fw) if weighted else (hits / n, *wilson(hits, n), n)  # unweighted: as before
                eff = f", effective n {n_eff:.0f}" if weighted else ""
                detail(f"  {label}: {hits} of {n} {of} ({p:.1%}, 95% CI {lo:.1%}–{hi:.1%}{eff})")
                out[key] = {"count": hits, "of": n, "rate": _r(p), "ci": [_r(lo), _r(hi)]}
                if weighted:
                    out[key]["effective_rows"] = _r(n_eff)
                return p
            rate_line(f"on gold ({basis})", "gold_rate", [(g, r.get("_w", 1.0)) for _, g, r in pairs], "rows")
            missed = rate_line("missed", "missed", [(g, r.get("_w", 1.0)) for f, g, r in pairs if not f], "rows the rule passed")
            alarms = rate_line("false alarms", "false_alarms", [(not g, r.get("_w", 1.0)) for f, g, r in pairs if f],
                               "rows the rule caught")
            if "max_missed" in conf:
                check(missed <= conf["max_missed"], f"missed {missed:.1%} (max {conf['max_missed']:.1%})", "max_missed", missed, conf["max_missed"])
            if "max_false_alarms" in conf:
                check(alarms <= conf["max_false_alarms"], f"false alarms {alarms:.1%} (max {conf['max_false_alarms']:.1%})",
                      "max_false_alarms", alarms, conf["max_false_alarms"])
    if by := (spec["metrics"][name] or {}).get("by"):
        out |= test_groups(by, conf, [(r, f) for r, f, _ in rows], pairs if qids else [], check)
    out["checks"] = check.log[first_check:]
    out["unavailable_checks"] = sorted(
        ({"max_missed", "max_false_alarms"} & set(conf)) - {c["check"] for c in out["checks"]})
    return out


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


def test_groups(by: str, conf: dict, rows: list[tuple[dict, bool]], pairs: list[tuple], check: "Checks") -> dict:
    """A metric per value of `by`: the rule's rate on answers in each group, with a 95% interval (rows are a sample
    of what the group produces, which is what a comparison of groups is about; weighted when the source has
    `weights`), and on gold where the metric has gold. `higher: [a, b]` checks that a's rate on answers is above
    b's: the 95% interval of the difference (Newcombe's, from the two Wilson intervals) must lie above 0."""
    groups: dict[str, list[tuple[bool, float]]] = {}
    gold: dict[str, list[tuple[bool, float]]] = {}
    for r, fired in rows:
        if r.get(by) not in (None, ""):
            groups.setdefault(str(r[by]), []).append((fired, r.get("_w", 1.0)))
    for _, g, r in pairs:
        if r.get(by) not in (None, ""):
            gold.setdefault(str(r[by]), []).append((g, r.get("_w", 1.0)))
    weighted = any(r.get("_w", 1.0) != 1.0 for r, _ in rows)
    stats = {g: wrate(fw) for g, fw in groups.items()}
    gstats = {g: wrate(gs) for g, gs in gold.items()}
    out: dict = {"by": by, "no_group": len(rows) - sum(map(len, groups.values())), "groups": {}}
    detail(f"  by {by}: on answers{', then on gold' if gold else ''} (95% CI{', weighted' if weighted else ''})")
    width, digits = max(map(len, groups), default=0), len(str(max(map(len, groups.values()), default=0)))
    for i, g in enumerate(sorted(groups)):
        fw, (p, lo, hi, n_eff) = groups[g], stats[g]
        k = sum(f for f, _ in fw)
        x = out["groups"][g] = {"rows": len(fw), "fired": k, "rate": _r(p), "ci": [_r(lo), _r(hi)]}
        line = f"{k:>{digits}} of {len(fw):<{digits}}  {p:6.1%} ({lo:.1%}–{hi:.1%})"
        if weighted:
            x["effective_rows"] = _r(n_eff)
            line += f"  effective n {n_eff:.0f}"
        if gs := gold.get(g):
            gp, glo, ghi, _ = gstats[g]
            x["gold_rate"] = {"count": sum(g for g, _ in gs), "of": len(gs), "rate": _r(gp), "ci": [_r(glo), _r(ghi)]}
            line += f"  gold {gp:.1%} ({glo:.1%}–{ghi:.1%})"
        if i < SHOW:
            detail(f"    {g:<{width}}  {line}")
    if len(groups) > SHOW:
        detail(f"    … {len(groups) - SHOW} more groups in the results file")
    if few := sum(len(fw) < FEW for fw in groups.values()):
        detail(f"    {few} of {len(groups)} groups have under {FEW} rows: their intervals are too wide to say much")
    if out["no_group"]:
        detail(f"    {out['no_group']} rows have no {by}, left out of the groups")
    if "higher" in conf:
        a, b = map(str, conf["higher"])
        if a not in stats or b not in stats:
            gone = next(g for g in (a, b) if g not in stats)
            check(False, f"{a} higher than {b}: no rows in group {gone!r} (groups: {', '.join(sorted(groups)[:SHOW])})", "higher", None, 0)
        else:
            d, lo, hi = newcombe(stats[a], stats[b])
            out["higher"] = {"groups": [a, b], "difference": _r(d), "ci": [_r(lo), _r(hi)]}
            check(lo > 0, f"{a} higher than {b}: {d * 100:+.1f} points (95% CI {lo * 100:+.1f} to {hi * 100:+.1f})", "higher", lo, 0)
            if lo > 0 and stats[a][1] <= stats[b][2]:
                detail("       the two groups' intervals overlap; the difference's interval does not include 0")
            if a in gstats and b in gstats:  # the answers' difference can come from the model erring more in one group
                gd, glo, ghi = newcombe(gstats[a], gstats[b])
                out["higher"]["gold"] = {"difference": _r(gd), "ci": [_r(glo), _r(ghi)]}
                detail(f"       on gold: {gd * 100:+.1f} points (95% CI {glo * 100:+.1f} to {ghi * 100:+.1f})"
                      + ("; gold does not show it" if lo > 0 and glo <= 0 else ""))
    return out


def test_examples(spec: dict, check: "Checks", all_stats: list) -> list[dict]:
    """Golden examples: inline rows whose answers are pinned, asked like any row (same redaction, clip and keys, so
    once cached they cost nothing). Each example is one check; a failure names what it got and what was expected."""
    examples = spec.get("examples") or []
    if not examples:
        return []
    items, owner = [], []
    for i, ex in enumerate(examples):
        row = {**ex["row"], spec["key"]: f"example-{i + 1}"}
        for qid, v in ex["expect"].items():
            it = item(spec, row, qid)
            it["expected"] = (frozenset({"yes" if is_yes(str(v)) else "no"}) if it["q"]["type"] == "noul"
                              else normalize_gold(it["q"], str(v)))
            items.append(it)
            owner.append(i)
    answers, stats = asyncio.run(fill(spec, open_store(spec), items))
    all_stats.append(stats)
    detail(f"\nexamples ({len(examples)})")
    out = []
    for i, ex in enumerate(examples):
        name = ex.get("name") or f"example {i + 1}"
        got = []
        for it in (it for it, o in zip(items, owner) if o == i):
            a = answers[it["key"]]
            label, p, _ = decide(a)
            got.append({"question": it["qid"], "expected": gold_str(it["expected"]), "got": label, "p": _r(p),
                        "passed": hit(it, a, "expected")})
        ok = all(g["passed"] for g in got)
        check.severity = ex.get("severity", "error")
        check(ok, f"{name}: " + ", ".join(f"{g['question']} {g['got']} {g['p']:.2f}" + ("" if g["passed"] else f" (expected {g['expected']})")
                                           for g in got), "example")
        out.append({"name": name, "passed": ok, "severity": check.severity, "answers": got})
    return out


def cmd_test(project: dict, args) -> None:
    from hunch import presentation

    results_path(project).unlink(missing_ok=True)  # never leave an older run's results looking current
    results = execute(project)
    check = Checks()
    all_stats = [r["stats"] for r in results.values()]
    names = [args.node] if args.node else project["order"]
    report: dict = {}
    for n in names:
        res, spec = results[n], project["nodes"][n]
        rep = report[n] = {"spec_hash": spec_hash(spec), "model": spec.get("model"), "rows": len(res["rows"]), "questions": {}}
        if len(project["nodes"]) > 1:
            where = f", where kept {len(res['rows'])} of {res['input']}" if "where" in spec else ""
            kind = f"union of {', '.join(spec['union'])}" if "union" in spec else f"{res['input']} rows in{where}"
            detail(f"\n══ {n} ({kind})")
        attach_gold(res["items"], load_reviews(spec))  # just before testing: a union shares its branches' items
        ungrade(project, n, res["items"])
        for qid in question_of(spec):
            its = [it for it in res["items"] if it["qid"] == qid]
            q = its[0]["q"] if its else asked(project, n, qid)
            rep["questions"][qid] = test_question(spec, qid, q, its, res["answers"], check, all_stats)
            if its and its[0]["q"].get("none"):
                k = sum(decide(res["answers"][it["key"]])[0] == NONE for it in its)
                detail(f"  declined ({NONE}): {k}/{len(its)} rows ({k / len(its):.1%})")
            esc = [it for it in its if it["key"] in res.get("escalated", {})]
            if its and "escalate" in its[0]["q"]:
                gold = [it for it in esc if it["gold"]]
                acc = f"; right on {sum(hit(it, res['answers'][it['key']]) for it in gold)}/{len(gold)} with gold" if gold else ""
                acting = sum(route(it["q"], res["answers"][it["key"]], it.get("path_p", 1.0)) == "act" for it in esc)
                detail(f"  escalated to {its[0]['q']['escalate']['model']}: {len(esc)}/{len(its)} rows use its answer, "
                       f"{acting} of them confident enough to act{acc}")
        for parent, labels in spec.get("_multi", {}).items():
            rep.setdefault("multi", {})[parent] = test_multi(spec, parent, labels, res, check)
        for mname, m in (spec.get("metrics") or {}).items():
            rep.setdefault("metrics", {})[mname] = test_metric(spec, mname, m["rule"], res, check)
        if spec.get("examples"):
            rep["examples"] = test_examples(spec, check, all_stats)
        rep["assessment"] = assessment(rep)
    stats = merge_stats(*all_stats)
    if settings.VERBOSE:
        print_stats(stats)
    write_results(project, report, check, stats)
    if getattr(args, "receipt", False):
        print(f"receipt: {write_receipt(project, report, check)}")
    presentation.test(project, args, report, stats, presentation.relative(results_path(project)))
    sys.exit(1 if check.failed else 0)


RESULTS_VERSION = 2  # checks now contain only configured acceptance checks; measurements remain separate


def ungrade(project: dict, name: str, items: list[dict]) -> None:
    """A distilled student (the judgment's engine, or a union branch's) is graded only on rows it never trained on."""
    spec = project["nodes"][name]
    for s in [project["nodes"][u] for u in spec["union"]] if "union" in spec else [spec]:
        if str(s.get("model", "")).startswith("distilled:"):
            from hunch import distill
            if k := distill.ungrade_trained(s, items):
                print(f"  {k} gold answers were training data for {s['model']}: not graded")


def cmd_distill(project: dict, args) -> None:
    """A small local model per question, trained on the answers already in the store; asks nothing."""
    from hunch import distill
    node = args.node or next((n for n in reversed(project["order"]) if "union" not in project["nodes"][n]), "")
    if node not in project["nodes"] or "union" in project["nodes"][node]:
        sys.exit(f"--node {node!r}: pick a judgment with questions ({', '.join(project['order'])})")
    out, report = distill.distill(project, node)
    rel = os.path.relpath(out, project["nodes"][node]["_dir"])
    print(f"{node}: distilled to {out}\n" + "\n".join(report))
    print(f"measure it (free, local): hunch test {args.path} --model distilled:{rel}\n"
          f"use it: model: distilled:{rel}, with escalate: {{model: {project['nodes'][node]['model']}}} and act "
          f"on each question, so answers it isn't sure of go to {project['nodes'][node]['model']}")


def cmd_docs(project: dict, args) -> None:
    from hunch.docs import write_docs  # the page generator is its own module; core stays the engine
    page = write_docs(project)
    if getattr(args, "open", False):
        import webbrowser
        webbrowser.open(page.resolve().as_uri())


def cmd_show(project: dict, args) -> None:
    from hunch.inspect import show
    show(project, args)


def results_path(project: dict) -> Path:
    """.hunch/target/<tested spec or folder, relative to the store's folder>.json: one file per spec or project, so
    projects sharing a store keep their own; with --model, the engine's suffix too (`triage__deepseek_deepseek_flash`);
    with --target, its name (`triage@dev`), so a dev test never replaces the results docs and CI read."""
    spec = project["nodes"][project["order"][0]]
    store = store_path(spec["_dir"])
    tested = Path(project["path"]).resolve()
    try:
        name = tested.relative_to(store.parent.parent).with_suffix("")
    except ValueError:  # tested outside the store's folder (HUNCH_STORE elsewhere)
        name = Path(tested.stem)
    suffix = spec.get("_table_suffix", "")
    suffix += f"@{settings.TARGET}" if settings.TARGET and not suffix.endswith(f"@{settings.TARGET}") else ""
    return store.parent / "target" / name.with_name(name.name + suffix).with_suffix(".json")


def write_results(project: dict, report: dict, check: "Checks", stats: dict) -> None:
    """`test` as data, read by CI, dashboards, agents and `hunch docs`; field names are stable within a version.
    Written only when `test` finishes: a run stopped by --max-cost leaves no file (the old one is removed at start)."""
    spec = project["nodes"][project["order"][0]]
    path = results_path(project)
    path.parent.mkdir(parents=True, exist_ok=True)
    doc = {"version": RESULTS_VERSION, "command": "test", "at": datetime.now(UTC).isoformat(timespec="seconds"),
           "git_sha": git_sha(spec["_dir"]) or None, "passed": not check.failed,
           "assessment": overall_assessment(report), "sample": settings.SAMPLE,
           "target": settings.TARGET, "cost": _r(stats.get("cost")), "judgments": report}
    path.write_text(json.dumps(doc, indent=1, ensure_ascii=False) + "\n")
    if os.environ.get("GITHUB_STEP_SUMMARY"):  # GitHub Actions: a table on the run's summary page
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a") as f:
            f.write(summary_markdown(doc, path.relative_to(store_path(spec["_dir"]).parent / "target").with_suffix("")) + "\n")


def receipt_path(project: dict) -> Path:
    """Beside what was tested: `<spec>.results.json`, or `results.json` in a project folder; under --model with the
    engine's suffix, as its tables have (`answer_support.results__ollama_qwen2_5_0_5b.json`)."""
    tested = Path(project["path"]).resolve()
    suffix = project["nodes"][project["order"][0]].get("_table_suffix", "")
    return (tested / f"results{suffix}.json") if tested.is_dir() else tested.with_name(f"{tested.stem}.results{suffix}.json")


def write_receipt(project: dict, report: dict, check: "Checks") -> Path:
    """`test --receipt`: the numbers a battery ships with, to commit beside its spec. Only what the answers decide
    (no time, cost, commit or hunch version), so running it again on the same answers changes nothing, even after
    an upgrade, and a diff is a change in what was measured."""
    path = receipt_path(project)
    doc = {"version": RESULTS_VERSION, "command": "test", "passed": not check.failed,
           "assessment": overall_assessment(report), "sample": settings.SAMPLE,
           "judgments": report}
    path.write_text(json.dumps(doc, indent=1, ensure_ascii=False) + "\n")
    return path


def summary_markdown(doc: dict, name: Path) -> str:
    """results.json as a short Markdown table: per question accuracy, range and failed checks; metrics and examples."""
    def failed(checks: list[dict], unavailable: list[str] | None = None) -> str:
        bad = [f"{c['check']}{' (warn)' if c['severity'] == 'warn' else ''}" for c in checks if not c["passed"]]
        missing = list(unavailable or [])
        labels = (["FAIL: " + ", ".join(bad)] if bad else []) + (["NOT ASSESSED: " + ", ".join(missing)] if missing else [])
        return "; ".join(labels) if labels else "passed" if checks else "no checks configured"
    headline = doc.get("assessment", "passed" if doc["passed"] else "failed").replace("_", " ")
    lines = [f"### {name}: {headline}" + (f" (sample of {doc['sample']})" if doc["sample"] else ""),
             "", "| | Accuracy | 95% range | Checks |", "|---|---|---|---|"]
    for j, v in doc["judgments"].items():
        for q, x in v["questions"].items():
            a = x.get("accuracy")
            ci = "–".join(f"{c:.1%}" for c in a["ci"]) if a and a["ci"] else ""
            state = failed(x.get("checks", []), x.get("unavailable_checks"))
            lines.append(f"| {j}.{q} | {a['value']:.1%} | {ci} | {state} |" if a else f"| {j}.{q} | no gold yet | | {state} |")
        for q, x in (v.get("multi") or {}).items():
            state = failed(x.get("checks", []), x.get("unavailable_checks"))
            exact = x.get("exact_set_accuracy")
            lines.append(f"| {j}.{q} (multi) | {exact:.1%} exact set | | {state} |" if exact is not None
                         else f"| {j}.{q} (multi) | no gold yet | | {state} |")
        for m, x in (v.get("metrics") or {}).items():
            miss = x.get("missed")
            extra = f", missed {miss['rate']:.1%} ({miss['ci'][0]:.1%}–{miss['ci'][1]:.1%})" if miss and miss["of"] else ""
            state = failed(x.get("checks", []), x.get("unavailable_checks"))
            lines.append(f"| {j}.{m} (metric) | fires on {x['rate']:.1%}{extra} | | {state} |" if x["rate"] is not None else f"| {j}.{m} (metric) | no rows | | {state} |")
        if v.get("examples"):
            ok = sum(e["passed"] for e in v["examples"])
            names = [e["name"] + (" (warn)" if e["severity"] == "warn" else "") for e in v["examples"] if not e["passed"]]
            lines.append(f"| {j} examples | {ok} of {len(v['examples'])} pass | | {'not passing: ' + ', '.join(names) if names else 'pass'} |")
    return "\n".join(lines) + "\n"


def test_multi(spec: dict, parent: str, labels: list[str], res: dict, check: "Checks") -> dict:
    """A multi question as a whole: is the set of options judged to apply exactly the gold set?"""
    by_row: dict[str, dict[str, dict]] = {}
    for it in res["items"]:
        if it["q"].get("_multi", [None])[0] == parent:
            by_row.setdefault(it["id"], {})[it["q"]["_multi"][1]] = it
    scored = [(its, frozenset(l for l, it in its.items() if decide(res["answers"][it["key"]])[0] == "yes"),
               frozenset(l for l, it in its.items() if it["gold"] and "yes" in it["gold"]))
              for its in by_row.values() if all(it["gold"] for it in its.values())]
    detail(f"\n{parent} (multi: {len(labels)} options, {len(by_row)} rows)")
    out: dict = {"type": "multi", "options": labels, "rows": len(by_row), "gold_rows": len(scored)}
    if not scored:
        conf = ((spec.get("tests") or {}).get(parent) or {})
        return out | {"checks": [], "unavailable_checks": ["min_accuracy"] if "min_accuracy" in conf else []}
    exact = sum(got == gold for _, got, gold in scored) / len(scored)
    jac = sum(len(got & gold) / len(got | gold) if got | gold else 1.0 for _, got, gold in scored) / len(scored)
    conf = ((spec.get("tests") or {}).get(parent) or {})
    check.severity = conf.get("severity", "error")
    first_check = len(check.log)
    if "min_accuracy" in conf:
        want = conf["min_accuracy"]
        check(exact >= want, f"exact-set accuracy {exact:.1%} on {len(scored)} rows with gold "
              f"(mean overlap {jac:.2f}) (min {want:.0%})", "min_accuracy", exact, want)
    else:
        detail(f"  exact-set accuracy {exact:.1%} on {len(scored)} rows with gold (mean overlap {jac:.2f})")
    return out | {"exact_set_accuracy": _r(exact), "mean_overlap": _r(jac), "checks": check.log[first_check:]}


def diff_question(qid: str, new_its: list[dict], old_its: list[dict], new_a: dict, old_a: dict,
                  same_spec: bool) -> tuple[dict[str, tuple[str | None, str | None]], str | None, int]:
    """Compare answers, returning row changes and a short gold-backed interpretation."""
    lab = lambda a: decide(a)[0] if a else None
    old_by = {it["id"]: it for it in old_its}
    new_ids = {it["id"] for it in new_its}
    pairs = [(old_by[it["id"]], it) for it in new_its if it["id"] in old_by]
    entered, left = len(new_ids - set(old_by)), len(set(old_by) - new_ids)
    moved = f"; {entered} rows newly reach it, {left} no longer do" if entered or left else ""
    if not old_its:
        detail(f"\n{qid}: new question ({len(new_its)} rows)")
        return {it["id"]: (None, lab(new_a.get(it["key"]))) for it in new_its}, None, 0
    if all(o["key"] == n["key"] for o, n in pairs) and not moved:
        detail(f"\n{qid}: unchanged (same keys, 0 calls)")
        return {}, None, 0
    flips = []
    for o, n in pairs:
        oa, na = old_a[o["key"]], new_a[n["key"]]
        (ol, _, om), (nl, _, nm) = decide(oa), decide(na)
        if ol != nl:
            flips.append((n, oa, na, min(om, nm) < NOISE))
    why = " (its own spec is unchanged: moved by upstream changes)" if same_spec and (flips or moved) else ""
    detail(f"\n{qid}: {len(flips)}/{len(pairs)} rows flip ({sum(f[3] for f in flips)} within noise band){moved}{why}")
    if pairs:  # how far and which way, not just which labels changed (re-asking one spec moves |p| ~0.01)
        noul = new_its[0]["q"]["type"] == "noul"
        prob = (lambda a, _: a["noul"]) if noul else (lambda a, lab: dict(ranked(a)).get(lab, 0.0))
        d = [prob(new_a[n["key"]], decide(old_a[o["key"]])[0]) - prob(old_a[o["key"]], decide(old_a[o["key"]])[0])
             for o, n in pairs]
        what = "p(yes)" if noul else "p(old answer)"
        detail(f"  probabilities moved: mean |Δ| {sum(map(abs, d)) / len(d):.3f}, mean Δ {what} {sum(d) / len(d):+.3f}")
    gold_note = None
    if flips and any(n["gold"] for _, n in pairs):
        fixed = sum(hit(n, na) and not hit(n, oa) for n, oa, na, _ in flips if n["gold"])
        broke = sum(hit(n, oa) and not hit(n, na) for n, oa, na, _ in flips if n["gold"])
        p = sign_test(fixed, broke)
        verdict = "significant" if p < 0.05 else "NOT significant: could be noise, get more gold rows"
        common = [n for _, n in pairs]
        gold_note = f"Gold on changed rows: {fixed} fixed, {broke} broken; paired p={p:.3f} ({verdict})."
        detail(f"  gold accuracy on the {sum(bool(n['gold']) for n in common)} shared rows with gold {accuracy([o for o, _ in pairs], old_a, qid):.1%} → "
              f"{accuracy(common, new_a, qid):.1%}  (✓ {fixed} fixed, ✗ {broke} broken, "
              f"{sum(1 for n, *_ in flips if n['gold']) - fixed - broke} wrong both times, "
              f"{sum(1 for n, *_ in flips if not n['gold'])} without gold)"
               f"\n  paired sign test p={p:.3f} → {verdict}")
    flips.sort(key=lambda f: f[3])  # real flips first, noise-band flips last
    for n, oa, na, noisy in flips[:SHOW]:
        g = n["gold"]
        mark = "✓" if g and hit(n, na) and not hit(n, oa) else ("✗" if g and hit(n, oa) and not hit(n, na) else " ")
        detail(f"  {mark} #{n['id']:>4} {show(oa):>36} → {show(na):<36}{' ~noise' if noisy else '       '} {label_of(n, 40)}")
    if len(flips) > SHOW:
        detail(f"  … {len(flips) - SHOW} more")
    return ({n["id"]: (lab(oa), lab(na)) for n, oa, na, _ in flips}
            | {it["id"]: (None, lab(new_a.get(it["key"]))) for it in new_its if it["id"] not in old_by}
            | {it["id"]: (lab(old_a.get(it["key"])), None) for it in old_its if it["id"] not in new_ids}), gold_note, sum(
                noisy for _, _, _, noisy in flips)


def exposure_rows(spec: dict, x: dict, changed: dict[str, dict]) -> set[str]:
    """Rows whose change this exposure's code can see: an answer it relies on appeared or disappeared."""
    uses, rows = uses_of(x), set()
    for q, moves in changed.items():
        multi = ((spec.get("questions") or {}).get(q) or {}).get("_multi")  # [parent, option] of a multi's yes/no
        if uses is None or q in uses:
            relied = uses and uses[q]
        elif multi and multi[0] in uses and (uses[multi[0]] is None or multi[1] in uses[multi[0]]):
            relied = None if uses[multi[0]] is None else ["yes"]  # relying on an option: seeing it in the set
        else:
            continue
        rows |= {i for i, (a, b) in moves.items() if relies_on(a, relied) or relies_on(b, relied)}
    return rows


def twin_of(n: str, names: list[str]) -> str | None:
    """n's counterpart on the other side: the same name, else the only one there (a renamed copy)."""
    return n if n in names else (names[0] if len(names) == 1 else None)


def load_against(project: dict, args) -> dict:
    """The --against project (old logic, or the live one in shadow mode), reading today's rows at its roots."""
    old = load_project_ref(args.against, args.path)
    for n in roots(old):
        twin = twin_of(n, roots(project))
        if twin:
            header = source_header(project["nodes"][twin])
            missing = [c for c in state_columns(old["nodes"][n]) if c not in header]
            if missing:
                sys.exit(f"--against's {n!r} reads {missing}, which {twin!r}'s rows don't have: these projects don't "
                         f"judge the same data, so there is nothing to compare row by row")
            old["nodes"][n]["source"] = absolute_source(project["nodes"][twin])
    if settings.TARGET:  # the same target on both sides (if the old specs define it): compare logic, not targets.
        apply_target(old, settings.TARGET)  # --model, if given, is the new side's engine only, as without a target
    return old


def cmd_diff(project: dict, args) -> None:
    """Old logic on today's data: the old project's root judgments read the same rows as today's.
    With --model and no --against: the same specs on their own engine vs on --model."""
    if not args.against:
        if not args.model:
            sys.exit("diff needs --against PATH or git:REF (the version to compare with), or --model ENGINE "
                     "(the same specs on another engine)")
        args.against = str(args.path)
    old = load_against(project, args)
    new_r, old_r = execute(project), execute(old)
    from hunch import presentation

    stats = merge_stats(*(r["stats"] for r in (*new_r.values(), *old_r.values())))
    print(f"Diff · {args.path} against {args.against}")
    if settings.VERBOSE:
        print_stats(stats)
    if args.node:
        pairs = [(args.node, twin_of(args.node, old["order"]))]
    elif len(project["nodes"]) == 1:
        pairs = [(project["order"][0], twin_of(project["order"][0], old["order"]))]
    else:
        pairs = [(n, n) for n in project["order"] if n in old["nodes"]]
        if not pairs:
            sys.exit(f"no judgment names in common: {project['order']} vs {old['order']}; pick one with --node")
    for n, o in pairs:
        if o is None:
            sys.exit(f"--against has no judgment matching {n!r}; it has {old['order']}")
        spec, ospec = project["nodes"][n], old["nodes"][o]
        same = spec_hash(spec) == spec_hash(ospec)
        if len(project["nodes"]) > 1 or n != o:
            print(f"\n{n}" + (f" vs {o}" if n != o else ""))
        reviews = load_reviews(spec)  # gold is about the data, so both sides use today's reviews
        attach_gold(new_r[n]["items"], reviews)
        attach_gold(old_r[o]["items"], reviews)
        ungrade(project, n, new_r[n]["items"])
        ungrade(old, o, old_r[o]["items"])
        old_qs, changed = question_of(ospec), {}
        for qid in question_of(spec):
            new_its = [it for it in new_r[n]["items"] if it["qid"] == qid]
            oq = qid
            if qid not in old_qs:  # renamed? question ids aren't in the cache key, so match by keys
                keys = {it["key"] for it in new_its}
                oq = next((q for q in old_qs if {it["key"] for it in old_r[o]["items"] if it["qid"] == q} & keys), qid)
                if oq != qid:
                    print(f"  {qid}: renamed from {oq!r}")
            old_its = [it for it in old_r[o]["items"] if it["qid"] == oq]
            moves, gold_note, noisy = diff_question(qid, new_its, old_its, new_r[n]["answers"], old_r[o]["answers"], same)
            changed[qid] = moves
            flips = [(rid, before, after) for rid, (before, after) in moves.items() if before is not None and after is not None]
            entered = sum(before is None for before, _ in moves.values())
            left = sum(after is None for _, after in moves.values())
            shared = len({it["id"] for it in new_its} & {it["id"] for it in old_its})
            summary = f"{qid}: {len(flips)}/{shared} shared rows changed answer"
            if entered or left:
                summary += f"; {entered} entered, {left} left"
            if noisy:
                summary += f"; {noisy} near the decision boundary"
            presentation.line(summary)
            for rid, before, after in flips[:5]:
                presentation.line(f"{rid}: {before} → {after}", "    ")
            if len(flips) > 5:
                presentation.line(f"{len(flips) - 5} more changed rows", "    ")
            if gold_note:
                presentation.line(gold_note, "    ")
        print_exposures(spec, changed)
    presentation.line(f"Answers: {stats['cached']} cached, {stats['asked']} asked · ${stats['cost']:.5f}")


def print_exposures(spec: dict, changed: dict[str, dict]) -> None:
    """What the change reaches outside hunch: per exposure, the rows whose answer its code sees differently."""
    hit, missed = [], []
    for x in spec.get("exposures") or []:
        rows = exposure_rows(spec, x, changed)
        what = f"{x['name']} ({x.get('kind', 'app')}, {uses_text(x)})"
        (hit.append(f"affects {what}: {len(rows)} row{'s' * (len(rows) != 1)}") if rows else missed.append(what))
    if missed:
        hit.append(f"not affected: {', '.join(missed)}; no answer {'it relies' if len(missed) == 1 else 'they rely'} on changed")
    if hit:
        print("\n" + "\n".join(hit))


def review_queue(items: list[dict], answers: dict, audit: int, other: dict | None = None) -> list[tuple[str, dict]]:
    """shadow (with --against): the other spec answers this row differently. Reviewing just these decides which
      spec is better (diff's paired test only needs gold where they differ), so they come first.
    disputed: model ≠ source answer key (a model error, or a gold error); all of them, most confident first.
    audit: a fixed random sample of rows where model = answer key (of every row, if there is no key), topped up to
      `audit` audited rows per question. Without it, reviewing only disputes can only move accuracy up.
    uncertain: no gold and below the act threshold (a human label makes it gold).
    Rows with a current verdict are done."""
    shadow, disputed, uncertain, pool = [], [], [], {}
    audited = Counter()
    has_key = {it["qid"] for it in items if it["raw_gold"]}
    for it in items:
        a = answers[it["key"]]
        agrees = bool(it["raw_gold"]) and hit(it, a, "raw_gold")
        if it["gold_src"] in ("review", "excluded"):
            audited[it["qid"]] += it["review_kind"] == "audit"
            continue
        if other and (it["id"], it["qid"]) in other:
            shadow.append(it)
        elif it["raw_gold"] and not agrees:
            disputed.append(it)
        else:
            if agrees or it["qid"] not in has_key:  # no answer key: the audit samples every row
                pool.setdefault(it["qid"], []).append(it)
            if not it["raw_gold"] and route(it["q"], a, it.get("path_p", 1.0)) == "review":
                uncertain.append(it)
    audits = [it for qid, members in pool.items()
              for it in sorted(members, key=lambda it: digest([it["qid"], it["id"]]))[: max(0, audit - audited[qid])]]
    chosen = {id(it) for it in audits}
    uncertain = [it for it in uncertain if id(it) not in chosen]
    disputed.sort(key=lambda it: -conf_of(it, answers[it["key"]]))
    uncertain.sort(key=lambda it: conf_of(it, answers[it["key"]]))
    return ([("shadow", it) for it in shadow] + [("disputed", it) for it in disputed] + [("audit", it) for it in audits]
            + [("uncertain", it) for it in uncertain])


def cmd_review(project: dict, args) -> None:
    name = pick(project, args.node)
    spec = project["nodes"][name]
    res = execute(project)[name]
    items, answers = res["items"], res["answers"]
    attach_gold(items, load_reviews(spec))
    other = {}  # (row id, question) → the --against spec's answer, where it differs from this one's
    if args.against:
        old = load_against(project, args)
        o = twin_of(name, old["order"])
        if o is None:
            sys.exit(f"--against has no judgment matching {name!r}; it has {old['order']}")
        ores = execute(old)[o]
        for oi in ores["items"]:
            other[(oi["id"], oi["qid"])] = ores["answers"][oi["key"]]
        by = {(it["id"], it["qid"]): answers[it["key"]] for it in items}
        other = {k: oa for k, oa in other.items() if k in by and decide(oa)[0] != decide(by[k])[0]}
    queue = review_queue(items, answers, args.audit, other)
    kinds = Counter(k for k, _ in queue)
    if not queue:
        print(f"{name}: nothing to review (every disagreement and spot check has a verdict, and no answer is below act)")
        return
    queue = queue[: args.limit] if args.limit else queue
    if args.list:
        print("review queue: " + ", ".join(f"{c} {k}" for k, c in kinds.items()))
        for kind, it in queue:
            vs = f"{show(other[(it['id'], it['qid'])])} → " if kind == "shadow" else ""
            print(f"  {kind:<9} #{it['id']:>5} {it['qid']:<10} {vs}{show(answers[it['key']]):<36} "
                  f"gold={gold_str(it['raw_gold']):<24} {label_of(it, 50)}")
        return

    reviewer = args.reviewer or getpass.getuser()
    width = min(shutil.get_terminal_size().columns, 100)
    tty = sys.stdout.isatty() and not os.environ.get("NO_COLOR")
    bold, dim = [(lambda s, c=c: f"\033[{c}m{s}\033[0m" if tty else s) for c in ("1", "2")]
    wrap = lambda s: textwrap.fill(s, width, initial_indent="  ", subsequent_indent="  ")
    print(f"{bold(name)}: {len(queue)} of {sum(kinds.values())} rows to review ("
          + ", ".join(f"{c} {REVIEW_KINDS[k]}" for k, c in kinds.items()) + f"). Each answer is saved to "
          f"{reviews_path(spec).name} as you go.")
    print(dim("Judge only from the text shown, as a stranger would. If you can only decide it because you know more "
              "than this (the session, what happened later), press c: that counts as missing context, not a model error."))
    done = 0
    for n, (kind, it) in enumerate(queue, 1):
        a = answers[it["key"]]
        model = decide(a)[0]
        keyed = sorted(it["raw_gold"] or [])
        marks = {}  # label → who says it is the answer
        if kind == "shadow":
            old_label = decide(other[(it["id"], it["qid"])])[0]
            marks = {old_label: "--against", model: "this spec"}
        elif kind == "disputed":
            marks = {**{k: "answer key" for k in keyed}, model: "model"}
        else:
            marks = {review_key(it, a): "answer key" if keyed else "model"}
        default = None if kind in ("shadow", "disputed") else next(iter(marks))
        ranking = ranked(a)
        shown = [(lab, p) for i, (lab, p) in enumerate(ranking) if i < 5 or lab in marks]
        shown += [(lab, 0.0) for lab in marks if lab not in dict(ranking)]  # a key label the model never gave
        w = min(max(len(lab) for lab, _ in shown), 40)

        rule = f"─── {n} of {len(queue)} · {it['qid']} · {REVIEW_KINDS[kind]} · #{it['id']} "
        print("\n" + bold(rule + "─" * max(0, width - len(rule))))
        for col in state_columns(it["spec"]):
            print(f"\n{bold(col.upper())}\n{wrap(label_of(it, 600, [col]))}")
        ins = it["aq"].get("instructions", "")  # structured (Pydantic AI's): the question and the option it asks about
        ins = " · ".join(str(ins[k]) for k in ("question", "option") if k in ins) if isinstance(ins, dict) else ins
        instructions = " ".join(str(ins).split())
        print("\n" + dim(wrap(instructions if len(instructions) <= 300 else instructions[:299] + "…")))
        for i, (lab, p) in enumerate(shown, 1):
            mark = f"  ← {marks[lab]}" if lab in marks else ""
            line = f"  {i:>2}  {lab[:40]:<{w}}  {'█' * round(p * 20):<20} {p:.2f}{mark}"
            print(bold(line) if mark else line)
        if len(ranking) > len(shown):
            print(dim(f"      … {len(ranking) - len(shown)} more; type an option's name to pick it"))
        choice = "Enter = agree with the marked answer · " if default else ""
        both = " · b both acceptable" if kind in ("shadow", "disputed") else ""
        print(dim(f"{choice}1-{len(shown)} pick{both} · c needs more context · a ambiguous · s skip · q quit"))
        while True:
            try:
                ans = input("> ").strip()
            except EOFError:
                ans = "q"
            if ans == "q":
                print(f"\n{done} answers saved to {reviews_path(spec).name}")
                return
            if ans == "s":
                break
            label = None
            if ans in ("a", "c"):
                verdict = {"a": "ambiguous", "c": "needs_context"}[ans]
            elif ans == "b" and kind == "shadow":
                verdict, label = "both_ok", f"{old_label}|{model}"
            elif ans == "b" and kind == "disputed":
                verdict, label = "both_ok", "|".join(keyed + [model])
            else:
                if ans == "" and default:
                    label = default
                elif ans.isdigit() and 1 <= int(ans) <= len(shown):
                    label = shown[int(ans) - 1][0]
                elif ans in dict(ranking):
                    label = ans
                else:
                    print("  ? " + ("pick a number" if ans == "" else "not an option"))
                    continue
                verdict = picked_verdict(kind, label, marks)
                if verdict == "key_right":
                    label = gold_str(it["raw_gold"])
            append_review(spec, {"qid": it["qid"], "row_id": it["id"], "state_hash": it["shash"], "kind": kind,
                                 "verdict": verdict, "label": label or "", "reviewer": reviewer,
                                 "at": datetime.now(UTC).isoformat(timespec="seconds")})
            done += 1
            break
    print(f"\n{done} answers saved to {reviews_path(spec).name}")


REVIEW_KINDS = {"shadow": "the two specs disagree", "disputed": "model ≠ answer key", "audit": "spot check",
                "uncertain": "model unsure"}


def review_key(it: dict, a: dict) -> str:
    """The answer a spot check asks you to confirm: the answer key's, or the model's where there is no key."""
    return gold_str(it["raw_gold"]) if it["raw_gold"] else decide(a)[0]


def picked_verdict(kind: str, label: str, marks: dict[str, str]) -> str:
    """What picking `label` says about the row, in load_reviews' verdict vocabulary."""
    who = marks.get(label)
    return {("shadow", "--against"): "against_right", ("shadow", "this spec"): "spec_right",
            ("disputed", "model"): "model_right", ("disputed", "answer key"): "key_right",
            ("audit", "answer key"): "confirmed", ("audit", "model"): "confirmed"}.get((kind, who), "labeled")

# ---------- suggest ----------

WRITER = "deepseek:deepseek-flash"


async def llm_text(model: str, prompt: str, temperature: float = 0.7) -> tuple[str, float]:
    """A plain completion from an LLM endpoint (for writing, not judging): (text, cost)."""
    ep = endpoint(model)
    if not os.environ.get(ep["key"]):
        raise SystemExit(f"set {ep['key']} for the writer ({model})")
    body = {"model": llm_route(model)[0], "messages": [{"role": "user", "content": prompt}],
            "max_tokens": 32000, "temperature": temperature}  # reasoning models think first: leave room
    if settings.MAX_COST is not None:  # worst case: the whole reply allowance used
        p_in, p_out = llm_prices(model)
        worst = len(prompt.encode()) * p_in + body["max_tokens"] * p_out  # at most a token per byte
        if worst > settings.MAX_COST:
            raise SystemExit(f"writer {model}: up to ${worst:.4f} per rewrite, above --max-cost ${settings.MAX_COST}; nothing asked")
    async with httpx.AsyncClient(headers={"Authorization": f"Bearer {os.environ[ep['key']]}"}, timeout=300) as client:
        j = await post(client, asyncio.Semaphore(1), f"{ep['base']}/chat/completions", body)
    u = j["usage"]
    cost = u.get("cost") if "cost" in u else u["prompt_tokens"] * ep["price"][0] + u.get("completion_tokens", 0) * ep["price"][1]
    c = j["choices"][0]
    if c.get("finish_reason") == "length":
        raise ValueError(f"the writer ran out of room ({u.get('completion_tokens')} tokens)")
    return c["message"]["content"] or "", cost


def writer_prompt(q: dict, mistakes: list[tuple[str, str, str]], k: int) -> str:
    shown = {k: v for k, v in q.items() if k in ("type", "instructions", "criteria", "none")}
    cases = "\n".join(f"- input: {text}\n  expected: {gold}\n  model answered: {got}" for text, gold, got in mistakes)
    return f"""You improve a classification question that a small judging model answers. The model reads the question
(instructions, and for choices the options with their descriptions) and an input, and picks an answer.

Current question (JSON):
{json.dumps(shown, ensure_ascii=False, indent=1)}

Cases it got wrong (expected = the correct answer):
{cases}

Rewrite the instructions and the option descriptions so a careful reader would answer these cases correctly,
without breaking the cases it already gets right. Describe what distinguishes confusable options; don't quote
the example inputs. Keep every option name exactly as it is, in the same order, and add or remove none.
This is variant {k}: take a different angle from an obvious rewrite if k > 0.

Reply with only a JSON object: {{"instructions": "...", "criteria": {{"<option>": "<description>", ...}}}}
(for a yes/no question, only "instructions")."""


def parse_rewrite(text: str, q: dict) -> dict:
    m = re.search(r"\{.*\}", text, re.S)
    if not m:
        raise ValueError("no JSON object in the reply")
    new = json.loads(m.group(0))
    out = {**q, "instructions": new["instructions"]}
    if q["type"] in ("choice", "score"):
        crit = new.get("criteria") or {}
        if list(crit) != list(q["criteria"]):
            raise ValueError(f"options changed ({len(crit)} vs {len(q['criteria'])}, or renamed / reordered)")
        out["criteria"] = crit
    return out


def spec_yaml(spec: dict) -> str:
    return yaml.safe_dump({k: v for k, v in spec.items() if not k.startswith("_") and not isinstance(v, Path)},
                          sort_keys=False, allow_unicode=True, width=120)


def cmd_suggest(project: dict, args) -> None:
    """Rewrites of one question, kept only when they win on gold they were not written from. Gold rows are
    split in two by a hash of their id: the writer sees the current spec's mistakes on one half; each rewrite
    is judged on the other half, paired against the current answers (sign test, Bonferroni over the n tried:
    the best of several looks better than it is). Rejected rewrites are reported too; each is saved as a spec
    under .hunch/suggest/. Confirm a kept one on a holdout before adopting it."""
    name = pick(project, args.node)
    spec = project["nodes"][name]
    qids = [args.question] if args.question else list(spec["questions"])
    if len(qids) != 1 or qids[0] not in spec["questions"]:
        sys.exit(f"pick one question with --question ({', '.join(spec['questions'])})")
    qid, q = qids[0], spec["questions"][qids[0]]
    res = execute(project)[name]
    its = [it for it in res["items"] if it["qid"] == qid]
    attach_gold(its, load_reviews(spec))
    gold = [it for it in its if it["gold"]]
    learn = [it for it in gold if int(digest(["split", it["id"]])[:8], 16) % 2 == 0]
    learn_ids = {it["id"] for it in learn}
    judge_on = [it for it in gold if it["id"] not in learn_ids]
    wrong = [it for it in learn if not hit(it, res["answers"][it["key"]])]
    print(f"{name}.{qid}: {len(gold)} rows with gold → {len(learn)} to learn from ({len(wrong)} mistakes), "
          f"{len(judge_on)} to judge on; writer {args.writer}")
    if not wrong:
        sys.exit("no mistakes to learn from")
    rnd = random.Random(0)
    shown = rnd.sample(wrong, min(len(wrong), 60))
    mistakes = [(label_of(it, 300), gold_str(it["gold"]), decide(res["answers"][it["key"]])[0])
                for it in shown]
    base = {it["id"]: res["answers"][it["key"]] for it in judge_on}
    base_acc = sum(hit(it, base[it["id"]]) for it in judge_on) / len(judge_on)
    out_dir = store_path(spec["_dir"]).parent / "suggest"
    out_dir.mkdir(exist_ok=True)
    writer_cost, kept = 0.0, []
    print(f"  current: {base_acc:.1%} on the judging half")
    for k in range(args.n):
        try:
            text, c = asyncio.run(llm_text(args.writer, writer_prompt(q, mistakes, k)))
            writer_cost += c
            nq = parse_rewrite(text, q)
        except (ValueError, KeyError, json.JSONDecodeError) as e:
            print(f"  #{k}: rejected, unusable rewrite ({e})")
            continue
        cspec = {**spec, "questions": {**spec["questions"], qid: nq}}
        path = out_dir / f"{name}.{qid}.{k}.yml"
        saved = {**cspec, "source": str(absolute_source(spec))} if "source" in spec else cspec  # readable from .hunch/
        path.write_text(f"# hunch suggest: rewrite {k} of {name}.{qid}, not yet adopted\n" + spec_yaml(saved))
        citems = [item(cspec, it["row"], qid) for it in judge_on]
        cans, cstats = asyncio.run(fill(cspec, open_store(cspec), citems))
        fixed = broke = 0
        for it, ci in zip(judge_on, citems):
            ci["gold"] = it["gold"]
            a, b = hit(it, base[it["id"]]), hit(ci, cans[ci["key"]])
            fixed, broke = fixed + (b and not a), broke + (a and not b)
        acc = sum(hit(ci, cans[ci["key"]]) for ci in citems) / len(citems)
        p = sign_test(fixed, broke)
        ok = p < 0.05 / args.n and fixed > broke  # Bonferroni: the best of n rewrites looks better than it is
        kept += [path] if ok else []
        print(f"  #{k}: {acc:.1%} ({acc - base_acc:+.1%}; ✓ {fixed} fixed, ✗ {broke} broken, p={p:.3f}) "
              f"{'KEPT' if ok else f'rejected: needs p < {0.05 / args.n:.3f} ({args.n} tried)'} → {path.name} "
              f"(${cstats['cost']:.4f})")
    print(f"  writer cost ${writer_cost:.4f}")
    if kept:
        print("  before adopting: `hunch diff <rewrite> --against <spec> --source <holdout>`: on BANKING77 a kept "
              "rewrite's +5.1% was +2.4% (n.s.) on the holdout")


# ---------- online ----------

_projects: dict[Path, dict] = {}


def roots(project: dict) -> list[str]:
    return [n for n in project["order"] if not upstream(project["nodes"][n])]


def log_traffic(project: dict, names: list[str], row: dict) -> None:
    """Rows judged online in shadow mode, so the candidate can be compared on real traffic (--traffic).
    One entry per distinct row and judgment name; `n` counts repeats."""
    db = open_store(project["nodes"][roots(project)[0]])
    db.execute("""create table if not exists traffic (judgment text, rhash text, row text, n integer,
        first_at text default current_timestamp, last_at text default current_timestamp,
        primary key (judgment, rhash))""")
    rules = redaction_rules(project["nodes"][roots(project)[0]])  # the live spec's redaction covers what is kept
    body = json.dumps({k: redact(canon(v), rules) for k, v in row.items()}, ensure_ascii=False)
    write(db, """insert into traffic (judgment, rhash, row, n) values (?, ?, ?, 1) on conflict do update
                 set n = n + 1, last_at = current_timestamp""", [(n, digest(body)[:16], body) for n in names])


def traffic_source(spec: dict) -> Path:
    """This judgment's logged traffic as a CSV, so every command (diff, test, review) can read it like a source.
    Rows without a key value get one from their content, stable across exports (reviews stay attached)."""
    db = open_store(spec)
    try:
        got = db.execute("select rhash, row from traffic where judgment = ? order by first_at, rhash",
                         (spec["judgment"],)).fetchall()
    except sqlite3.OperationalError:
        got = []
    if not got:
        sys.exit(f"{spec['judgment']}: no logged traffic (judge(..., shadow=...) logs it)")
    out = [{spec["key"]: f"t{h}", **json.loads(r)} for h, r in got]
    path = store_path(spec["_dir"]).parent / "traffic" / f"{spec['judgment']}.csv"
    path.parent.mkdir(exist_ok=True)
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, list(dict.fromkeys(k for r in out for k in r)))
        w.writeheader()
        w.writerows(out)
    return path


_background: set = set()  # shadow candidates still running inside an app's event loop


def _project(path: str | Path) -> dict:
    q = Path(path).resolve()
    return _projects.get(q) or _projects.setdefault(q, load_project(q))


async def _shadow(path: str | Path, fields: dict) -> None:
    try:
        await aexecute(_project(path), rows_in=[fields])
    except (Exception, SystemExit) as e:  # a failing candidate (even one that won't load) never fails the live answer
        print(f"shadow {path}: {e!r}", file=sys.stderr)


def _shadow_names(live: dict, shadow: str | Path) -> list[str]:
    try:
        return [n for n in roots(_project(shadow)) if n not in roots(live)]
    except (Exception, SystemExit) as e:
        print(f"shadow {shadow}: {e!r}", file=sys.stderr)
        return []


async def ajudge(path: str | Path, row: dict | None = None, /, *, node: str | None = None,
                 shadow: str | Path | None = None, log: bool = False, **fields) -> dict | None:
    """Judge one row inside an app: the whole project runs on it, same keys as batch (a row the batch already
    judged is a cache hit; a row judged online is a hit for the next batch). Returns {judgment: {question:
    answer}}; a judgment the row never reached (its where-clause said no) is None. For a one-judgment project,
    or with `node`, returns just that judgment's answers.

    shadow: a candidate project that answers the same row after the live answer is returned (a background task
    in this event loop; never returned, never fails the live answer). Its answers are cached, so
    `hunch diff CANDIDATE --against LIVE --traffic` compares them on real traffic for free.
    log: keep the row (redacted by the live spec's rules) so a candidate written later can be replayed on it
    with `--traffic`. Shadowing implies logging.
    The row is `row` (a dict: use it when a column is named node, shadow or log) and/or keyword fields."""
    fields = {**(row or {}), **fields}
    project = _project(path)
    results = await aexecute(project, rows_in=[fields])
    if shadow or log:
        log_traffic(project, roots(project) + (_shadow_names(project, shadow) if shadow else []), fields)
    if shadow:
        task = asyncio.get_running_loop().create_task(_shadow(shadow, fields))
        _background.add(task)
        task.add_done_callback(_background.discard)
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
            label, conf, margin = decide(a)
            # p: probability of the label (hunch's vocabulary); margin: distance from the decision boundary,
            # |p - 0.5| x 2 for yes/no, top minus runner-up for choices (Pydantic AI's `confidence`)
            out[n][it["qid"]] = {"label": label, "p": conf_of(it, a), "margin": round(margin, 4),
                                 "route": route(it["q"], a, it["path_p"]), "cached": it["key"] in res["hits"]}
    if node:
        return out[node]
    return next(iter(out.values())) if len(out) == 1 else out


_SHADOWS: list[threading.Thread] = []


def _join_shadows() -> None:
    for t in _SHADOWS:
        t.join()


# At exit, Python runs these hooks (last registered first) and only then joins non-daemon threads. One of them,
# concurrent.futures', stops every thread pool, and asyncio resolves hosts through one: a shadow still asking
# failed with "cannot schedule new futures after interpreter shutdown" and its answer was lost. Registered after
# concurrent.futures.thread is imported (at the top of this file), this joins the shadows before that happens.
threading._register_atexit(_join_shadows)


def judge(path: str | Path, row: dict | None = None, /, *, node: str | None = None, shadow: str | Path | None = None,
          log: bool = False, **fields) -> dict | None:
    """Sync ajudge. A shadow candidate runs in a thread after the live answer returns; the thread is not a
    daemon, so a script waits for it at exit and the candidate's answers are recorded."""
    fields = {**(row or {}), **fields}
    out = asyncio.run(ajudge(path, fields, node=node, log=log or bool(shadow)))
    if shadow:  # the row was logged under the live names; add the candidate's own
        extra = _shadow_names(_project(path), shadow)
        if extra:
            log_traffic(_project(path), extra, fields)
        t = threading.Thread(target=lambda: asyncio.run(_shadow(shadow, fields)), name="hunch-shadow")
        _SHADOWS.append(t)
        t.start()
    return out


def main() -> None:
    commands = {"lint": cmd_lint, "compile": cmd_compile, "run": cmd_run, "test": cmd_test, "suggest": cmd_suggest,
                "diff": cmd_diff, "review": cmd_review, "docs": cmd_docs, "distill": cmd_distill,
                "show": cmd_show}
    descriptions = {
        "lint": "Check a spec without asking the model.",
        "compile": "Preview a request and estimate the cost of a run.",
        "run": "Judge rows, cache answers, and write tables.",
        "show": "Inspect the last materialized run without asking the model.",
        "test": "Measure answers against gold and evaluate configured checks.",
        "diff": "Compare today's answers with an older spec or another model.",
        "review": "Build gold by reviewing disagreements and spot checks.",
        "docs": "Generate a shareable HTML report from the last test.",
        "suggest": "Try and measure question rewrites.",
        "distill": "Train a small local model from cached answers.",
    }
    p = argparse.ArgumentParser(prog="hunch", description="Run, inspect, and measure model judgments.",
                                epilog="Other commands: hunch ask, init, plugins, install, skill, hook.")
    p.add_argument("--version", action="version", version=f"hunch {__import__('hunch').__version__}")
    sub = p.add_subparsers(dest="command", required=True, metavar="COMMAND")
    for command, desc in descriptions.items():
        sp = sub.add_parser(command, help=desc, description=desc)
        sp.add_argument("path", type=Path, help="a spec file or folder of specs")
        if command in {"compile", "run", "show", "test", "diff", "review", "suggest", "distill"}:
            sp.add_argument("--node", help="one judgment in a project")
        if command in {"diff", "review"}:
            sp.add_argument("--against", help="old spec/project path or git:REF")
        if command in {"lint", "compile", "run", "test", "diff", "review", "suggest", "distill"}:
            sp.add_argument("--source", type=Path, help="use this CSV for the root judgments")
            sp.add_argument("--traffic", action="store_true", help="use rows logged by judge(..., shadow=...)")
        if command in {"compile", "run", "test", "diff", "review", "suggest", "distill"}:
            sp.add_argument("--max-cost", type=float, help="maximum USD to spend on missing answers")
        if command in {"compile", "run", "test", "diff"}:
            sp.add_argument("--sample", type=int, help="judge a repeatable sample of N root rows")
        if command in {"run", "test", "diff"}:
            sp.add_argument("--verbose", action="store_true", help="show diagnostic tables and request details")
        sp.add_argument("--model", help="use another engine; keeps its tables separate")
        sp.add_argument("--target", help="use settings from targets.NAME in the spec")
        if command == "run":
            sp.add_argument("--allow-change", action="store_true", help="accept a spec change under on_change: freeze")
        if command == "test":
            sp.add_argument("--receipt", action="store_true", help="write a stable results file beside the spec")
        if command == "review":
            sp.add_argument("--list", action="store_true", help="print the queue without prompting")
            sp.add_argument("--limit", type=int, help="review at most N items")
            sp.add_argument("--audit", type=int, default=30, help="random agreeing rows to audit (default 30)")
            sp.add_argument("--reviewer", help="name recorded with each verdict")
        if command == "show":
            sp.add_argument("--id", help="show all stored fields for one row key")
            sp.add_argument("--limit", type=int, default=20, help="rows to list (default 20)")
        if command == "docs":
            sp.add_argument("--open", action="store_true", help="open the generated HTML in a browser")
        if command == "suggest":
            sp.add_argument("--question", help="question to rewrite")
            sp.add_argument("--n", type=int, default=3, help="rewrites to try (default 3)")
            sp.add_argument("--writer", default=WRITER, help=f"LLM that writes rewrites (default {WRITER})")
    if len(sys.argv) == 1:
        p.print_help()
        return
    args = p.parse_args()
    for name, default in {"node": None, "against": None, "source": None, "traffic": False, "max_cost": None,
                          "sample": None, "verbose": False, "receipt": False}.items():
        if not hasattr(args, name):
            setattr(args, name, default)
    settings.SAMPLE, settings.TARGET, settings.STORE = args.sample, None, None
    settings.VERBOSE = args.verbose
    settings.MAX_COST = args.max_cost if args.max_cost is not None else settings.MAX_COST  # else $HUNCH_MAX_COST, if set
    project = load_project(args.path)
    project["target"] = args.target
    if args.model:  # another engine on the same specs: its tables get a suffix, the spec's own stay untouched
        for spec in project["nodes"].values():
            spec["_table_suffix"] = "__" + re.sub(r"\W+", "_", args.model).strip("_")
            if "union" not in spec:
                spec["model"] = args.model
    if args.source and args.traffic:
        sys.exit("--source or --traffic, not both")
    for n in roots(project):
        if args.source:
            project["nodes"][n]["source"] = args.source.resolve()
        elif args.traffic:
            project["nodes"][n]["source"] = traffic_source(project["nodes"][n])
    errors, warnings = (lint_targets(project), []) if args.command == "show" else lint(project)
    if args.traffic:  # live traffic has no gold columns: expected, so one line instead of one per question
        nogold = [w for w in warnings if "gold column" in w]
        warnings = [w for w in warnings if w not in nogold]
        if nogold:
            print(f"note: logged traffic has no gold ({len(nogold)} questions); `hunch review --traffic` builds it",
                  file=sys.stderr)
    for w in warnings:
        print(f"lint warning: {w}", file=sys.stderr)
    for e in errors:
        print(f"lint error: {e}", file=sys.stderr)
    if errors:
        sys.exit(2)
    if args.target:
        if args.receipt:
            sys.exit("--receipt records the spec as written; run it without --target")
        run = apply_target(project, args.target, keep_model=bool(args.model))
        settings.TARGET, settings.STORE = args.target, run.get("store")  # None: the usual store
        settings.SAMPLE = args.sample if args.sample is not None else run.get("sample")
        if args.max_cost is None and "max_cost" in run:  # a cap: $HUNCH_MAX_COST still holds if it is lower
            settings.MAX_COST = run["max_cost"] if settings.MAX_COST is None else min(settings.MAX_COST, run["max_cost"])
    commands[args.command](project, args)


MOVED = {"SAMPLE", "TARGET", "STORE", "VERBOSE", "MAX_COST", "CHARGED", "SPEND_LIMIT", "RETRIES"}


class _Core(type(sys)):
    """Before 0.4 these lived here, and `hunch.core.MAX_COST = 0.0` was the documented cap. Setting one now would set a
    name nothing reads, and a run meant to be capped wouldn't be: so reading or setting one is an error naming
    hunch.settings."""
    def __getattr__(self, name):
        raise AttributeError(f"hunch.core.{name} moved to hunch.settings.{name}" if name in MOVED
                             else f"module 'hunch.core' has no attribute {name!r}")

    def __setattr__(self, name, value):
        if name in MOVED:
            raise AttributeError(f"hunch.core.{name} moved: set hunch.settings.{name}")
        super().__setattr__(name, value)


sys.modules[__name__].__class__ = _Core

if __name__ == "__main__":
    main()
