"""`hunch lint`: every check that can run before anything is asked."""

import difflib
import re
from pathlib import Path

from hunch import engines
from hunch.answers import act_needed, normalize_gold
from hunch.spec import (
    ENDPOINTS,
    EXPOSURE_KEYS,
    EXPOSURE_KINDS,
    METRIC_TEST_KEYS,
    NONE,
    ON_CHANGE,
    QUESTION_KEYS,
    REDACTIONS,
    RESERVED,
    SEVERITIES,
    SPEC_KEYS,
    TARGET_KEYS,
    TEST_KEYS,
    UNION_KEYS,
    answer_columns,
    asked,
    bare_names,
    compile_baseline,
    compile_where,
    source_header,
    source_kind,
    state_columns,
    upstream,
)


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
