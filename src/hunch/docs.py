"""`hunch docs`: the project as one page people can read and search, and as manifest.json for tools.

Asks nothing and costs nothing. It reads the specs, the last `hunch test` of the same path (results.json) and the
store's run log, and shows only numbers `test` produced. A judgment whose spec changed after that test is marked
stale rather than shown with numbers that describe an older version.
"""
import html
import json
import re
import sqlite3
from datetime import datetime
from pathlib import Path
from urllib.parse import quote

from hunch import core

MANIFEST_VERSION = 1
STATUS = {  # status → (label, what it means), in the order the inventory lists them: what needs a look first
    "fail": ("failing", "a check failed at the last test"),
    "warn": ("warning", "a check with severity warn failed at the last test"),
    "stale": ("stale", "the spec changed after its last test, so its numbers describe an older version"),
    "noresults": ("no results", "the last hunch test of this path did not include it"),
    "nogold": ("no gold", "tested, but nothing to measure against yet: no answer key, reviews or examples"),
    "ok": ("ok", "tested, passing, and the spec has not changed since"),
}
SHARE_CHECKS = {"min_accuracy", "min_act_accuracy", "order_stability", "min_rate", "max_rate", "max_missed",
                "max_false_alarms", "expected answer"}  # checks whose value is a share of rows, shown as a percent


# ---------- data ----------

def manifest(project: dict) -> dict:
    """The project as data: each judgment's spec as written (a multi question stays one question), plus its file,
    its spec hash (what `test` records) and the judgments it reads. Nothing here comes from answers."""
    nodes, root = project["nodes"], Path(project["path"])
    base = root if root.is_dir() else root.parent
    out = {}
    for n in project["order"]:
        s = nodes[n]
        f = Path(s["_file"]) if s.get("_file") else None
        spec = {k: v for k, v in s.items() if not k.startswith("_")}
        if "_written" in s:
            spec["questions"] = s["_written"]
        out[n] = {"file": str(f.relative_to(base)) if f and f.is_relative_to(base) else (str(f) if f else None),
                  "spec_hash": core.spec_hash(s), "upstream": core.upstream(s), **spec}
    first = nodes[project["order"][0]]
    return {"version": MANIFEST_VERSION, "git_sha": core.git_sha(first["_dir"]) or None,
            "project": root.stem if root.is_file() else root.name, "judgments": out}


def runs(project: dict) -> dict[str, list[dict]]:
    """The last runs of each judgment, newest first, from the store's run log (read-only; none if no store)."""
    first = project["nodes"][project["order"][0]]
    path = core.store_path(first["_dir"])
    if not path.exists():
        return {}
    db = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    db.row_factory = sqlite3.Row
    if not db.execute("select 1 from sqlite_master where name = '_hunch_runs'").fetchone():
        return {}
    return {n: [dict(r) for r in db.execute("select * from _hunch_runs where judgment = ? order by finished_at desc limit 5",
                                            (core.table_name(s),))] for n, s in project["nodes"].items()}


def checks_of(r: dict) -> list[dict]:
    """Every check in one judgment's results, each with `on`: the question, metric or example it belongs to."""
    cs = [c | {"on": name} for g in ("questions", "multi", "metrics") for name, x in (r.get(g) or {}).items()
          for c in x.get("checks", [])]
    return cs + [{"on": f"example {x['name']}", "check": "expected answer", "passed": x["passed"],
                  "severity": x.get("severity", "error")} for x in r.get("examples") or []]


def status(m: dict, r: dict | None) -> str:
    if r is None:
        return "noresults"
    if r["spec_hash"] != m["spec_hash"]:
        return "stale"
    checks = checks_of(r)
    failed = [c for c in checks if not c["passed"]]
    if any(c.get("severity", "error") == "error" for c in failed):
        return "fail"
    if failed:
        return "warn"
    measured = (any(q.get("accuracy") for q in r["questions"].values()) or r.get("examples") or checks
                or any(x.get("gold") for x in (r.get("metrics") or {}).values()))
    return "ok" if measured else "nogold"


# ---------- words ----------

def pct(x: float | None) -> str:
    return "–" if x is None else f"{x:.0%}" if x in (0, 1) or abs(x * 100 - round(x * 100)) < 0.05 else f"{x:.1%}"


def local(ts: str | None) -> str:
    """An ISO time as the reader's local date and minute (runs are logged in local time already)."""
    if not ts:
        return ""
    try:
        t = datetime.fromisoformat(ts)
    except ValueError:
        return ts[:16]
    return (t.astimezone() if t.tzinfo else t).strftime("%Y-%m-%d %H:%M")


CHECK_WORDS = {  # check → (what it measures, "min" or "max")
    "min_accuracy": ("accuracy", "min"), "min_act_accuracy": ("accuracy among answers it acts on alone", "min"),
    "max_calibration_error": ("calibration error", "max"), "min_auroc": ("AUROC", "min"),
    "order_stability": ("answers that flip when options are reordered", "max"), "min_rate": ("rate", "min"),
    "max_rate": ("rate", "max"), "max_missed": ("miss rate", "max"), "max_false_alarms": ("false-alarm rate", "max"),
}


def check_words(c: dict) -> str:
    """"billing_share rate is above its maximum: 35% observed, 20% allowed" rather than a raw check key."""
    if c["check"] == "expected answer":
        return f"{c['on']} did not give the expected answer"
    what, side = CHECK_WORDS.get(c["check"], (c["check"], ""))
    fmt = lambda v: pct(v) if c["check"] in SHARE_CHECKS and isinstance(v, (int, float)) else f"{v:.3f}" if isinstance(v, float) else str(v)
    bound = {"min": ("below its minimum", "required"), "max": ("above its maximum", "allowed")}.get(side, ("outside its limit", "limit"))
    tail = f": {fmt(c['value'])} observed, {fmt(c['limit'])} {bound[1]}" if c.get("value") is not None else ""
    return f"{c['on']} {what} is {bound[0]}{tail}"


def count(share: float | None, of: int) -> int:
    return round((share or 0) * of)


def evidence(n: str, m: dict, r: dict | None) -> list[dict]:
    """One row per question, metric and example set, all at the same weight: what was measured, on what basis,
    and what failed. Failing rows first. Built only from results.json."""
    rows = []
    rq = (r or {}).get("questions", {})
    for qid in (m.get("questions") or {}):
        parts = [k for k in rq if k == qid or k.startswith(qid + "__")]  # a multi question is tested per option
        q = rq.get(qid) or {}
        failed = [c | {"on": qid} for k in parts for c in rq[k].get("checks", []) if not c["passed"]]
        a = q.get("accuracy")
        if a and a["basis"] == "estimate":
            reviewed = sum(v["reviewed"] for v in a.get("reviewed", {}).values())
            lo, hi = a["ci"]
            what, fig = f"estimated from {reviewed} reviewed rows", (a["value"], a)
            words = f"{pct(a['value'])} right, {pct(lo)}–{pct(hi)}"
        elif a:
            g = q["gold"]["rows"]
            what, fig = "agree with the answer key", (a["value"], a)
            words = f"{count(a['value'], g)} of {g} rows"
        else:
            what, fig = ("answered, not tested per question" if parts and not q else "no answer key or reviews"), None
            words = "not measured"
        act = q.get("act")
        if act:
            t = " / ".join(f"{k} {v}" for k, v in act["threshold"].items()) if isinstance(act["threshold"], dict) else act["threshold"]
            done = count(act["automated"], q["rows"])
            wrong = ("none checkable yet" if act["wrong"] is None else "none wrong" if act["wrong"] == 0
                     else f"{count(act['wrong'], act['judged'])} of {act['judged']} checked wrong")
            act = f"at act {t}: acts alone on {done} of {q['rows']} rows, {wrong}"
        rows.append({"id": qid, "anchor": qanchor(n, qid), "kind": "question", "value": words, "basis": what,
                     "act": act, "fig": fig, "failed": failed})
    for k, x in ((r or {}).get("metrics") or {}).items():
        failed = [c | {"on": k} for c in x.get("checks", []) if not c["passed"]]
        extra = [f"{w} {count(v['rate'], v['of'])} of {v['of']} ({pct(v['ci'][0])}–{pct(v['ci'][1])})"
                 for key, w in (("missed", "missed"), ("false_alarms", "false alarms")) if (v := x.get(key)) and v.get("rate") is not None]
        rows.append({"id": k, "anchor": None, "kind": "metric", "value": f"{x['fired']} of {x['rows']} rows ({pct(x['rate'])})"
                     if x.get("rate") is not None else "no rows", "basis": f"rule {x['rule']}", "act": "; ".join(extra) or None,
                     "fig": None, "failed": failed})
    if ex := (r or {}).get("examples"):
        failed = [{"on": f"example {x['name']}", "check": "expected answer", "passed": False, "severity": x.get("severity", "error")}
                  for x in ex if not x["passed"]]
        rows.append({"id": "pinned examples", "anchor": None, "kind": "examples", "value": f"{sum(x['passed'] for x in ex)} of {len(ex)} pass",
                     "basis": "rows whose answers are pinned in the spec", "act": None, "fig": None, "failed": failed})
    return sorted(rows, key=lambda x: not x["failed"])


# ---------- lineage ----------

def source_node(m: dict) -> tuple[str, str, str] | None:
    """(id, label, full text) of a root judgment's source; None for one that reads other judgments."""
    if m["upstream"] or m.get("_hide_source"):  # _hide_source: a mini lineage cut off this node's own inputs
        return None
    kind, value = core.source_kind(m)
    return "src:" + value, (Path(value).name if kind == "csv" else value) or value, value


def lineage(man: dict) -> dict:
    """Nodes (sources, judgments, exposures) placed in columns by depth, edges between them. Laid out here, drawn as
    SVG, so the page needs no graph library."""
    nodes, edges, depth = {}, [], {}
    for n, m in man["judgments"].items():  # manifest order is dependency order
        depth[n] = 1 + max((depth[u] for u in m["upstream"]), default=0)
        if src := source_node(m):
            nodes.setdefault(src[0], {"id": src[0], "kind": "source", "label": src[1], "title": src[2], "col": 0})
            edges.append((src[0], n))
        nodes[n] = {"id": n, "kind": "judgment", "label": n, "col": depth[n]}
        edges += [(u, n) for u in m["upstream"]]
    for n, m in man["judgments"].items():  # an exposure sits just right of its judgment: its edge never passes behind a box
        for x in m.get("exposures") or []:
            xid = "exp:" + x["name"]
            nd = nodes.setdefault(xid, {"id": xid, "kind": "exposure", "label": x["name"], "sub": x.get("kind", "app"), "col": 0})
            nd["col"] = max(nd["col"], depth[n] + 1)
            edges.append((n, xid))
    cols: dict[int, list[str]] = {}
    for nid, nd in nodes.items():
        cols.setdefault(nd["col"], []).append(nid)
    parents: dict[str, list[str]] = {}
    for a, b in edges:
        parents.setdefault(b, []).append(a)
    for c in sorted(cols):  # order each column by its parents' positions: fewer crossings, no library
        cols[c].sort(key=lambda nid: sum(nodes[p]["row"] for p in parents[nid]) / len(parents[nid]) if parents.get(nid) else 0)
        for i, nid in enumerate(cols[c]):
            nodes[nid]["row"] = i
    return {"nodes": list(nodes.values()), "edges": edges}


def lineage_svg(lin: dict, st: dict[str, str], mini: bool = False) -> str:
    W, H, BW, BH = (200, 56, 164, 38) if mini else (236, 64, 184, 42)
    size = {c: sum(nd["col"] == c for nd in lin["nodes"]) for c in {nd["col"] for nd in lin["nodes"]}}
    tall = max(size.values(), default=1)  # short columns are centred against the tallest
    pos = {nd["id"]: (16 + nd["col"] * W, 16 + (nd["row"] + (tall - size[nd["col"]]) / 2) * H) for nd in lin["nodes"]}
    width = max((x for x, _ in pos.values()), default=0) + BW + 32
    height = max((y for _, y in pos.values()), default=0) + BH + 32
    out = [f'<svg class="dag" viewBox="0 0 {width} {height}" data-full="0 0 {width} {height}" role="img" aria-label="lineage"'
           + (f' style="max-width:{width}px"' if mini else "") + ">"]
    for a, b in lin["edges"]:
        (x1, y1), (x2, y2) = pos[a], pos[b]
        x1, y1, y2 = x1 + BW, y1 + BH / 2, y2 + BH / 2
        mid = (x1 + x2) / 2
        out.append(f'<path class="edge" data-a="{e(a)}" data-b="{e(b)}" d="M{x1},{y1} C{mid},{y1} {mid},{y2} {x2},{y2}"/>')
    for nd in lin["nodes"]:
        x, y = pos[nd["id"]]
        label = nd["label"] if len(nd["label"]) <= 22 else nd["label"][:21] + "…"
        judg = nd["kind"] == "judgment"
        cls = f'node {nd["kind"]}' + (f' st-{st[nd["id"]]}' if judg else "")
        sub = {"source": "source", "exposure": nd.get("sub", "app")}.get(nd["kind"]) or STATUS[st[nd["id"]]][0]
        bar = f'<rect class="bar-l" width="4" height="{BH}" rx="2"/>' if judg else ""
        out.append(f'<g class="{cls}" data-id="{e(nd["id"])}" transform="translate({x},{y})" tabindex="{0 if judg else -1}">'
                   f'<title>{e(nd.get("title", nd["label"]))}</title><rect width="{BW}" height="{BH}" rx="7"/>{bar}'
                   f'<text x="14" y="17">{e(label)}</text><text class="sub" x="14" y="32">{e(sub)}</text></g>')
    return "\n".join(out + ["</svg>"])


def neighbourhood(man: dict, n: str) -> dict:
    """One step up and down from `n`, plus its exposures, as a small manifest for a mini lineage."""
    js = man["judgments"]
    near = {n, *js[n]["upstream"], *(d for d, dm in js.items() if n in dm["upstream"])}
    sub = {}
    for k in js:  # keep dependency order
        if k in near:
            m = dict(js[k])
            m["upstream"] = [u for u in m["upstream"] if u in near]
            if k != n:
                m["exposures"] = []
                if m["upstream"] == [] and js[k]["upstream"]:
                    m = {**m, "upstream": [], "_hide_source": True}
            sub[k] = m
    return {"judgments": sub}


# ---------- page ----------

PAGE = (Path(__file__).parent / "docs_page.html").read_text
MARK = ('<svg viewBox="0 0 96 96" aria-hidden="true"><path fill="#7D969B" d="M16 8H44V23H25V73H40V88H16C12 88 9 85 9 81V15C9 '
        '11 12 8 16 8Z M80 8H57V24H72V73H55V88H80C84 88 87 85 87 81V15C87 11 84 8 80 8Z"/></svg>')


def e(x) -> str:
    return html.escape("" if x is None else str(x))


def text_of(v) -> str:
    return v if isinstance(v, str) else json.dumps(v, ensure_ascii=False, indent=1)


def pill(st: str) -> str:
    return f'<span class="pill st-{st}" title="{e(STATUS[st][1])}">{e(STATUS[st][0])}</span>'


def decides(m: dict) -> str:
    if m.get("description"):
        return m["description"]
    if "union" in m:
        return f"merges {m.get('question')} from {', '.join(m['union'])}"
    first = next(iter((m.get("questions") or {}).values()), None)
    return text_of(first.get("instructions")) if first else ""


def options_of(q: dict) -> list[tuple[str, object]]:
    c = q.get("criteria")
    rows = list(c.items()) if isinstance(c, dict) else [(str(i), v) for i, v in enumerate(c)] if isinstance(c, list) else []
    return rows + ([(core.NONE, q["none"])] if q.get("none") else [])


def search_text(n: str, m: dict) -> str:
    qs = m.get("questions") or {}
    return json.dumps({
        "name": n, "description": m.get("description") or "",
        "questions": " ".join(f"{qid} {text_of(q.get('instructions'))}" for qid, q in qs.items()),
        "options": " ".join(f"{k} {text_of(v)}" for q in qs.values() for k, v in options_of(q)),
        "columns": " ".join([*m.get("state", []), m.get("key") or ""]), "source": str(m.get("source", "")),
        "used by": " ".join(f"{x['name']} {x.get('kind', '')} {x.get('owner', '')} {x.get('description', '')}"
                            for x in m.get("exposures") or [])})


def ordered(js: dict, st: dict) -> list[str]:
    return sorted(js, key=lambda n: (list(STATUS).index(st[n]), n))


def readme(project: dict) -> str:
    """The first paragraph of the folder's README, as plain text: the project's own words for the overview."""
    root = Path(project["path"])
    f = (root if root.is_dir() else root.parent) / "README.md"
    if not f.exists():
        return ""
    paras = [p.strip() for p in f.read_text().split("\n\n")]
    first = next((p for p in paras if p and not p.startswith(("#", "```", "|", "<", "!", "-", "*"))), "")
    return " ".join(first.split()).replace("**", "").replace("`", "")


def output_columns(spec: dict) -> list[tuple[str, str]]:
    """The table this judgment writes, column by column: what apps and downstream judgments read."""
    out = [(spec.get("key") or "", "the row's id (the spec's key)"), ("…", "every input column, as it arrived")]
    for parent, labels in (spec.get("_multi") or {}).items():
        out.append((parent, f"the options that apply, joined by |: any of {', '.join(labels)}"))
    for qid, q in spec.get("questions", {}).items():
        if "_multi" in q:
            what = f"yes or no: does {q['_multi'][1]} apply"
        else:
            what = {"choice": "the chosen option", "noul": "yes or no", "score": "the nearest level, as level:label"}.get(q["type"], "")
        out += [(qid, what), (f"{qid}_p", "confidence in that answer, 0 to 1")]
        if q["type"] == "noul":
            out.append((f"{qid}_pyes", "probability of yes"))
        if "act" in q:
            out.append((f"{qid}_route", "act (confident enough to use) or review"))
        if "escalate" in q:
            out.append((f"{qid}_by", "the engine whose answer is used"))
        out.append((f"{qid}_key", "the answer's address in the store (lineage)"))
    out.append(("_hunch_run_id", "the run that wrote this row"))
    return out


def yaml_html(text: str) -> str:
    """The spec as written, with keys and comments marked: enough colour to scan, no highlighter library."""
    out = []
    for line in text.splitlines():
        body, _, comment = line.partition(" #") if not line.lstrip().startswith("#") else ("", "", line)
        t = e(body)
        t = re.sub(r"^(\s*(?:- )?)([\w.-]+):", r'\1<span class="k">\2</span>:', t)
        out.append(t + (f'<span class="c">{" #" if body else ""}{e(comment)}</span>' if comment else ""))
    return "\n".join(out)


def request_example(spec: dict) -> tuple[str, str]:
    """(note, text): the shape of one request, as `compile` would print it, with the row's fields as placeholders.
    No real row: the page is meant to be shared, and a row can hold customer text."""
    state = {c: f"<{c}>" for c in spec.get("state", [])}
    aqs = {qid: core.api_question(q) for qid, q in spec.get("questions", {}).items()}
    if not aqs:
        return "A union asks nothing: it merges its branches' answers.", ""
    if core.is_llm(spec["model"]):
        qid = next(iter(aqs))
        return (f"{spec['model']} gets one request per question; this is the prompt for {qid}.",
                core.llm_prompt(aqs[qid], state)[0])
    return ("One request per row: every question reads the row once.",
            json.dumps({"model": spec["model"], "state": state, "questions": aqs}, indent=2, ensure_ascii=False))


def dial_table(q: dict) -> str:
    d = q.get("dial")
    if not d:
        return ""
    if q["type"] == "noul":
        head = "<tr><th>act at</th><th>yes: automated</th><th>wrong</th><th>no: automated</th><th>wrong</th></tr>"
        body = "".join(f"<tr><td>{r['threshold']}</td><td>{pct(r['yes']['automated'])}</td><td>{pct(r['yes']['wrong'])}</td>"
                       f"<td>{pct(r['no']['automated'])}</td><td>{pct(r['no']['wrong'])}</td></tr>" for r in d)
    else:
        head = "<tr><th>act at</th><th>automated</th><th>wrong among automated</th></tr>"
        body = "".join(f"<tr><td>{r['threshold']}</td><td>{pct(r['automated'])}</td><td>{pct(r['wrong'])}</td></tr>" for r in d)
    return ("<p class='note'>Counted over the rows with a known answer, so shares can differ from the figures above, which "
            f"count every row. Pick <code>act</code> where the wrong share is one you can live with.</p><table class='data num'>{head}{body}</table>")


def anchor_href(anchor: str) -> str:
    return "#" + quote(anchor, safe="")


def href(n: str) -> str:
    return anchor_href("j-" + n)


def qanchor(n: str, qid: str) -> str:
    return f"q-{n}--{qid}"


def link(n: str) -> str:
    return f'<a href="{href(n)}" class="mono">{e(n)}</a>'


def interval(a: dict) -> str:
    """Drawn only when there is a range: the track is 0–100%, the band the 95% interval, the dot the estimate."""
    ci = a.get("ci")
    if not ci:
        return ""
    x = lambda f: 4 + f * 152  # 160 wide, 4 of margin each side so the dot never clips
    return (f'<svg class="iv" viewBox="0 0 160 14" role="img" aria-label="95% interval {e(pct(ci[0]))} to {e(pct(ci[1]))}">'
            f'<rect class="track" x="4" y="5" width="152" height="4" rx="2"/>'
            f'<rect class="band" x="{x(ci[0]):.1f}" y="3" width="{max(x(ci[1]) - x(ci[0]), 2):.1f}" height="8" rx="4"/>'
            f'<circle class="pt" cx="{x(a["value"]):.1f}" cy="7" r="3.5"/></svg>')


def acc_cell(r: dict | None, sample: int | None) -> str:
    """The weakest measured question, with its basis: the one a reader should know about."""
    if not r:
        return '<span class="muted">–</span>'
    qs = [(qid, q) for qid, q in r["questions"].items() if q.get("accuracy")]
    if not qs:
        return '<span class="muted">not measured</span>'
    qid, q = min(qs, key=lambda x: x[1]["accuracy"]["value"])
    a = q["accuracy"]
    basis = (f"{pct(a['ci'][0])}–{pct(a['ci'][1])}, reviewed" if a["basis"] == "estimate"
             else f"{count(a['value'], q['gold']['rows'])} of {q['gold']['rows']} agree with key")
    more = f" · lowest of {len(qs)}" if len(qs) > 1 else ""
    return (f'<div class="acc"><span class="v">{pct(a["value"])} <small>{e(basis)}</small></span>{interval(a)}'
            f'<span class="muted" style="font-size:12px"><span class="mono">{e(qid)}</span>{more}'
            + (f" · sample of {sample}" if sample else "") + "</span></div>")


def sidebar(man: dict, st: dict) -> str:
    """Every judgment, grouped by status, on every screen. Search narrows it; status filters never do."""
    js = man["judgments"]
    groups = []
    for k in STATUS:
        names = [n for n in ordered(js, st) if st[n] == k]
        if names:
            groups.append(f'<section><h4>{e(STATUS[k][0])}</h4>' + "".join(
                f'<a href="{href(n)}" data-name="{e(n)}" data-search="{e(search_text(n, js[n]))}">'
                f'<span class="dot st-{k}"></span>{e(n)}</a>' for n in names) + "</section>")
    return (f'<nav class="tree" aria-label="Judgments">{"".join(groups)}</nav><p class="empty-side" id="none-side" hidden>No match.</p>'
            '<nav class="side-links" aria-label="Views"><a href="#home">Overview</a><a href="#lineage">Lineage</a></nav>')


def home(man: dict, res: dict, st: dict, run: dict, sample: int | None, about: str, at: str | None) -> str:
    js = man["judgments"]
    counts = {k: sum(v == k for v in st.values()) for k in STATUS}
    bar = "".join(f'<span class="st-{k}" style="flex:{c}" title="{c} {e(STATUS[k][0])}"></span>' for k, c in counts.items() if c)
    legend = "".join(f'<button type="button" data-st="{k}" aria-pressed="false"><span class="dot st-{k}"></span><b>{c}</b>'
                     f'<span class="lab">{e(STATUS[k][0])}</span></button>' for k, c in counts.items() if c)
    rows = []
    for n in ordered(js, st):
        m, r = js[n], res.get(n)
        failing = [check_words(c) for c in checks_of(r or {}) if not c["passed"]] if st[n] in ("fail", "warn") else []
        reason = failing[0] + (f" (+{len(failing) - 1} more)" if len(failing) > 1 else "") if failing else ""
        rows.append(
            f'<tr class="st-{st[n]}" data-st="{st[n]}" data-search="{e(search_text(n, m))}">'
            f'<td><a class="name" href="{href(n)}">{e(n)}</a><div style="margin-top:6px">{pill(st[n])}</div></td>'
            f'<td class="decides">{e(decides(m))}' + (f'<div class="reason">{e(reason)}</div>' if reason else "")
            + f'<div class="why"></div></td><td>{acc_cell(r, sample)}</td>'
            f'<td class="hide-sm">{e(", ".join(x["name"] for x in m.get("exposures") or [])) or "<span class=muted>–</span>"}</td></tr>')
    tested = f"last test {e(local(at))}" + (f" · sample of {sample} rows" if sample else "") if at else "no test results for this path yet"
    return f"""<section id="home" class="home">
<p class="eyebrow">Project</p><h1>{e(man['project'])}</h1>
{f'<p class="lede">{e(about)}</p>' if about else ''}
<div class="meta"><span>{len(js)} judgment{'s' * (len(js) != 1)}</span><span>{tested}</span>{f"<span>git {e(man['git_sha'])}</span>" if man['git_sha'] else ''}</div>
<div class="health"><div class="bar" role="img" aria-label="{e(', '.join(f'{c} {STATUS[k][0]}' for k, c in counts.items() if c))}">{bar}</div>
<div class="legend" role="group" aria-label="Show only one status">{legend}<button type="button" class="reset" hidden>Show all</button></div></div>
<div class="table-wrap"><table class="inv"><thead><tr><th>Judgment</th><th>Decides</th><th>Accuracy</th><th class="hide-sm">Used by</th></tr></thead>
<tbody>{''.join(rows)}</tbody></table><p class="empty" id="none" hidden>No judgment matches. <button type="button" class="reset linkish">Clear search and filter</button></p></div>
</section>"""


def status_summary(m: dict, r: dict | None, st: str, at: str | None, run: list[dict], sample: int | None, ev: list[dict]) -> str:
    """One short statement of the current result and the next useful step; the numbers live in the evidence list."""
    if st in ("fail", "warn"):
        bad = [(row, c) for row in ev for c in row["failed"]]  # named and linked here; the numbers are in the evidence row
        what = ", ".join(f'<a href="{anchor_href("ev-" + row["id"])}">{e(c["on"])}</a>' for row, c in bad)
        head = "Failing at the last test" if st == "fail" else "A check marked warn failed at the last test"
        body = f"<p><b>{head}:</b> {what}. The evidence below has the observed values and limits.</p>"
    elif st == "stale":
        body = (f"<p><b>The spec changed after its last test</b> ({e(local(at))}). The evidence below describes the older "
                f"version (<code>{e(r['spec_hash'])}</code>; now <code>{e(m['spec_hash'])}</code>). Run <code>hunch test</code>.</p>")
    elif st == "noresults":
        ran = f" It last ran {e(local(run[0].get('finished_at')))}." if run else ""
        body = ("<p><b>No test results for this judgment.</b> The last <code>hunch test</code> of this path did not include it: "
                f"never tested, tested alone with <code>--node</code> or through another path, or stopped by <code>--max-cost</code>.{ran}</p>")
    elif st == "nogold":
        body = ("<p><b>Tested, but nothing to measure against.</b> Add an answer key column (<code>gold:</code>) or review rows "
                "with <code>hunch review</code>.</p>")
    else:
        body = "<p><b>Passing</b> every configured check. That says the checks hold, not that every answer is right.</p>"
    if sample and r:
        body += f"<p>Measured on a sample of {sample} rows (<code>--sample {sample}</code>), not on every row.</p>"
    return f'<div class="status st-{st}" role="status">{body}</div>'


def evidence_html(ev: list[dict], stale: bool) -> str:
    if not ev:
        return ""
    rows = []
    for x in ev:
        name = f'<a href="{anchor_href(x["anchor"])}" class="mono">{e(x["id"])}</a>' if x["anchor"] else f'<span class="mono">{e(x["id"])}</span>'
        kind = "" if x["kind"] == "question" else f' <span class="muted">{e(x["kind"])}</span>'
        mark = "✗" if any(c.get("severity", "error") == "error" for c in x["failed"]) else "!" if x["failed"] else ""
        fails = "".join(f'<div class="fail">{e(check_words(c))}</div>' for c in x["failed"])
        rows.append(f'<li id="ev-{e(x["id"])}" class="{"bad" if x["failed"] else ""}"><div class="ev-name">{f"<span class=mk>{mark}</span>" if mark else ""}{name}{kind}</div>'
                    f'<div class="ev-val"><b>{e(x["value"])}</b> <span class="muted">{e(x["basis"])}</span>'
                    + (interval(x["fig"][1]) if x["fig"] else "")
                    + (f'<div class="muted">{e(x["act"])}</div>' if x["act"] else "") + f"{fails}</div></li>")
    title = "Evidence from the older version" if stale else "Evidence"
    return f'<div><h2>{title}</h2><ul class="evidence">{"".join(rows)}</ul></div>'


def question_block(n: str, qid: str, q: dict, r: dict | None) -> str:
    opts = options_of(q)
    parts = [f"<h3 id='{e(qanchor(n, qid))}'>{e(qid)}</h3><p class='qtext'>{e(text_of(q.get('instructions')))}</p>"]
    if opts:
        parts.append("<div class='scroll'><table class='data opts'>" + "".join(
            f"<tr><td><code>{e(k)}</code></td><td>{e(text_of(v))}</td></tr>" for k, v in opts) + "</table></div>")
    esc = (q.get("escalate") or {}).get("model")
    meta = [f"type <code>{e(q.get('type'))}</code>"] + ([f"acts alone at confidence <code>{e(q['act'])}</code>"] if "act" in q else []) \
        + ([f"answer key column <code>{e(q['gold'])}</code>"] if q.get("gold") else []) \
        + ([f"uncertain answers re-asked of <code>{e(esc)}</code>"] if esc else [])
    parts.append(f"<p class='note'>{' · '.join(meta)}</p>")
    if r:
        if r.get("dial"):
            parts.append(f"<details><summary>Where to set act</summary>{dial_table(r)}</details>")
        cal = ([f"Calibration error {r['calibration_error']:.3f}: 0 means stated confidence matches how often it is right."]
               if r.get("calibration_error") is not None else [])
        cal += [f"AUROC {r['auroc']:.3f}: how well p(yes) separates yes from no; 0.5 is a coin toss."] if r.get("auroc") is not None else []
        if cal:
            parts.append(f"<details><summary>Calibration</summary><p class='note'>{'<br>'.join(e(c) for c in cal)}</p></details>")
        mk = r.get("mistakes") or {}
        if mk.get("total"):
            parts.append(f"<details><summary>Most confident mistakes ({mk['total']})</summary><p class='note'>Wrong with high "
                         "confidence: a dangerous mistake, or a wrong answer key. <code>hunch review</code> shows them first.</p>"
                         "<div class='scroll'><table class='data'><tr><th>row</th><th>got</th><th>p</th><th>key says</th></tr>" + "".join(
                             f"<tr><td class='mono'>{e(x['id'])}</td><td>{e(x['got'])}</td><td class='num'>{e(x['p'])}</td>"
                             f"<td>{e(' | '.join(x['gold']))}</td></tr>" for x in mk["most_confident"]) + "</table></div></details>")
    return "<div class='question'>" + "".join(parts) + "</div>"


def judgment_page(n: str, m: dict, spec: dict, r: dict | None, st: str, run: list[dict], at: str | None, sample: int | None,
                  downstream: list[str], mini: str) -> str:
    ev = evidence(n, m, r)
    reads = f"<code>{e(src[2])}</code>" if (src := source_node(m)) else " + ".join(link(u) for u in m["upstream"])
    exposures = "".join(
        f"<li>{e(x['name'])} <span class='muted'>{e(x.get('kind', 'app'))}{' · ' + e(x['owner']) if x.get('owner') else ''}"
        f"{' · reads ' + e(', '.join(x['uses'])) if x.get('uses') else ' · reads every answer'}</span>"
        + (f" <a href='{e(x['url'])}' rel='noopener'>open</a>" if str(x.get("url", "")).startswith(("https://", "http://")) else "")
        + (f"<div class='muted'>{e(x['description'])}</div>" if x.get("description") else "") + "</li>"
        for x in m.get("exposures") or [])
    rail = (f"<dl class='facts'><dt>Reads</dt><dd>{reads}" + (f"<div class='muted'>where <code>{e(m['where'])}</code></div>" if m.get("where") else "")
            + f"</dd><dt>Model sees</dt><dd>{', '.join(f'<code>{e(c)}</code>' for c in m.get('state', [])) or '–'}"
            + (f"<div class='muted'>removed first: {e(', '.join(m['redact']))}</div>" if m.get("redact") else "")
            + f"</dd><dt>Feeds</dt><dd>{', '.join(link(d) for d in downstream) or '–'}</dd>"
            f"<dt>Used by</dt><dd>{f'<ul class=plain>{exposures}</ul>' if exposures else '–'}</dd>"
            f"<dt>Engine</dt><dd><code>{e(m.get('model'))}</code></dd></dl>"
            f"<div class='dagbox mini'>{mini}</div><a href='#lineage/{quote(n, safe='')}' class='small'>Open in the full lineage</a>")
    union = (f"<p>Merges the answers to <code>{e(m.get('question'))}</code> from {', '.join(link(u) for u in m['union'])}.</p>"
             if "union" in m else "")
    qs = "".join(question_block(n, qid, q, (r or {}).get("questions", {}).get(qid)) for qid, q in (m.get("questions") or {}).items())
    metrics = "".join(f"<li><code>{e(k)}</code>: <code>{e((v or {}).get('rule'))}</code></li>" for k, v in (m.get("metrics") or {}).items())
    runs_html = "".join(f"<tr><td>{e((x.get('finished_at') or '')[:16].replace('T', ' '))}</td><td>{e(x.get('rows'))}</td>"
                        f"<td>{e(x.get('asked'))}</td><td>${(x.get('cost') or 0):.4f}</td><td class='muted'>{e(x.get('status'))}</td></tr>"
                        for x in run)
    cols = "".join(f"<tr><td><code>{e(c)}</code></td><td>{e(w)}</td></tr>" for c, w in output_columns(spec))
    f = spec.get("_file")
    note, req = request_example(spec)
    title = m.get("description") or n
    tech = [("Output columns", f"<p class='note'>The table this judgment writes to the store, <code>{e(core.table_name(spec))}</code>: "
             f"what an app or a downstream judgment reads.</p><div class='scroll'><table class='data'><tr><th>column</th><th>holds</th></tr>{cols}</table></div>"),
            ("Spec", f"<p class='note'><code>{e(m['file'])}</code> · spec hash <code>{e(m['spec_hash'])}</code></p>"
             f"<pre>{yaml_html(Path(f).read_text()) if f and Path(f).exists() else ''}</pre>"),
            ("Request", f"<p class='note'>{e(note)} The row's fields are placeholders.</p>" + (f"<pre>{e(req)}</pre>" if req else ""))]
    if runs_html:
        tech.append(("Recent runs", f"<div class='scroll'><table class='data num'><tr><th>finished</th><th>rows</th><th>asked</th>"
                                    f"<th>cost</th><th>status</th></tr>{runs_html}</table></div>"))
    return f"""<section id="j-{e(n)}" class="judgment">
<p class="crumb"><a href="#home">Overview</a> / judgment</p>
<div class="jhead"><h1{' class="is-id"' if title == n else ''}>{e(title)}</h1></div>
<div class="meta"><span class="mono id">{e(n)}</span>{pill(st)}<span>{f"tested {e(local(at))}" if r else "no test results"}</span><span>spec <code>{e(m['file'])}</code></span></div>
<div class="grid"><div class="flow">
{status_summary(m, r, st, at, run, sample, ev)}
{evidence_html(ev, st == "stale")}
<div><h2>Questions and criteria</h2>{union}{qs or "<p class='note'>No questions of its own.</p>"}</div>
{f'<div><h2>Metrics</h2><ul class="summary">{metrics}</ul></div>' if metrics and not (r or {}).get("metrics") else ''}
<div><h2>Technical reference</h2>{''.join(f'<details><summary>{k}</summary>{v}</details>' for k, v in tech)}</div>
</div><aside class="rail" aria-label="Inputs and consumers"><p class="eyebrow">Inputs and consumers</p>{rail}</aside></div>
</section>"""


def page(project: dict, man: dict, results: dict | None, run: dict, st: dict[str, str]) -> str:
    res, at, sample = (results or {}).get("judgments", {}), (results or {}).get("at"), (results or {}).get("sample")
    js = man["judgments"]
    down = {n: [d for d, dm in js.items() if n in dm["upstream"]] for n in js}
    pages = "".join(judgment_page(n, m, project["nodes"][n], res.get(n), st[n], run.get(n, []), at, sample, down[n],
                                  lineage_svg(lineage(neighbourhood(man, n)), st, mini=True)) for n, m in js.items())
    lin = ('<section id="lineage"><p class="eyebrow">Project</p><h1 style="font-size:34px">Lineage</h1>'
           '<p class="lede">Sources on the left, then judgments, then what uses them. Select a judgment to light up what it reads '
           'from and what depends on it; select it again to open it. Scroll to zoom, drag to move.</p>'
           '<div class="dagbox big" style="margin-top:18px"><div class="tools"><button type="button" data-z="in">Zoom in</button>'
           '<button type="button" data-z="out">Zoom out</button><button type="button" data-z="fit">Fit</button></div>'
           f'{lineage_svg(lineage(man), st)}</div>'
           '<details class="edges"><summary>Every connection, as text</summary><ul class="plain">'
           + "".join(f"<li>{e(a.split(':', 1)[-1])} → {e(b.split(':', 1)[-1])}</li>" for a, b in lineage(man)["edges"])
           + '</ul></details></section>')
    made = "Generated " + datetime.now().astimezone().strftime("%Y-%m-%d %H:%M") + " by hunch docs from the last hunch test of this path."
    fill = {"{{title}}": e(man["project"]), "{{mark}}": MARK, "{{sidebar}}": sidebar(man, st), "{{generated}}": e(made),
            "{{body}}": home(man, res, st, run, sample, readme(project), at) + pages + lin}
    out = PAGE()
    for k, v in fill.items():
        out = out.replace(k, v)
    return out


def write_docs(project: dict) -> None:
    rp = core.results_path(project)
    results = json.loads(rp.read_text()) if rp.exists() else None
    man = manifest(project)
    st = {n: status(m, (results or {}).get("judgments", {}).get(n)) for n, m in man["judgments"].items()}
    mpath, hpath = rp.with_suffix(".manifest.json"), rp.with_suffix(".html")
    mpath.parent.mkdir(parents=True, exist_ok=True)
    mpath.write_text(json.dumps(man, indent=1, ensure_ascii=False, default=str) + "\n")
    hpath.write_text(page(project, man, results, runs(project), st))
    counts = [f"{list(st.values()).count(k)} {v[0]}" for k, v in STATUS.items() if k in st.values()]
    print(f"{len(st)} judgment{'s' * (len(st) != 1)}: " + ", ".join(counts))
    print(f"  {hpath}\n  {mpath}")
