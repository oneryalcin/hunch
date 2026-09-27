"""Specs and the project graph: loading, the where-language, sources, redaction and the state a model sees."""

import ast
import csv
import hashlib
import json
import operator
import re
import subprocess
import sys
from pathlib import Path

import yaml

from hunch import engines, settings

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


NOISE = 0.10  # measured run-to-run sd ~0.03 on ambiguous choices; flips inside this margin are flagged


DIAL = [0.5, 0.6, 0.7, 0.8, 0.9, 0.95, 0.99]


SHOW = 12  # rows listed per section; summaries always cover everything


def detail(*args, **kwargs) -> None:
    """The full diagnostic report, requested with --verbose."""
    if settings.VERBOSE:
        print(*args, **kwargs)


RESERVED = {"answers", "traffic"}  # the store's own table; a judgment of that name would drop the cache when materialized


# kind = why the row was reviewed: "audit" (random sample of agreements) | "disputed" | "uncertain". Only audits may
# stand in for unreviewed agreeing rows; rows picked for any other reason are not a random sample of anything.
# attach_gold also marks "same_text": a row given another live row's verdict because their text is identical.



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


# A spec's `model` picks the engine: "jev-…" is TypeSafe's System One; "<endpoint>:<id>" is an LLM read through
# its first answer token's log-probabilities. Both return the same answer shapes, so everything downstream
# (routing, tests, diff, review) is engine-neutral, and the model is part of every cache key.

OPENROUTER = "https://openrouter.ai/api/v1"


ENDPOINTS = {  # OpenAI-compatible chat APIs that return top logprobs
    "openrouter": {"base": OPENROUTER, "key": "OPENROUTER_API_KEY"},  # prices from its model listing
    "deepseek": {"base": "https://api.deepseek.com", "key": "DEEPSEEK_API_KEY", "body": {"thinking": {"type": "disabled"}},
                 "price": (0.15e-6, 0.60e-6)},  # list price per token (in, out); cache hits and off-peak cost less
}


LLM_ADAPTER = "logprobs-v1"  # part of an LLM answer's key: temperature 1, top-20, numbered options, question first


def is_llm(model: str) -> bool:
    return model.split(":", 1)[0] in ENDPOINTS


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
