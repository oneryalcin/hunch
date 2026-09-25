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


def accuracy_words(a: dict, gold: dict) -> str:
    if a["basis"] == "estimate":
        lo, hi = a["ci"]
        reviewed = sum(v["reviewed"] for v in a.get("reviewed", {}).values())
        return f"right on an estimated {pct(a['value'])} of rows ({pct(lo)}–{pct(hi)}), from {reviewed} reviewed rows"
    return f"agrees with the answer key on {pct(a['value'])} of the {gold['rows']} rows that have one"


def act_words(q: dict) -> str | None:
    a = q.get("act")
    if not a:
        return None
    t = a["threshold"]
    t = " / ".join(f"{k} {v}" for k, v in t.items()) if isinstance(t, dict) else t
    wrong = ("none of those has a known answer yet" if a["wrong"] is None
             else f"none of the {a['judged']} with a known answer was wrong" if a["wrong"] == 0
             else f"{pct(a['wrong'])} of the {a['judged']} with a known answer were wrong")
    return f"at act {t} it acts alone on {pct(a['automated'])} of rows, and {wrong}"


def metric_words(name: str, x: dict) -> str:
    out = f"{name} (metric): fires on {pct(x['rate'])} of {x['rows']} rows"
    for k, what in (("missed", "missed"), ("false_alarms", "false alarms")):
        if (v := x.get(k)) and v.get("rate") is not None:
            out += f"; {what} {pct(v['rate'])} ({pct(v['ci'][0])}–{pct(v['ci'][1])}, {v['of']} rows with gold)"
    return out + "."


def summary(r: dict | None) -> list[str]:
    """The judgment in a few sentences, made only of what `test` measured."""
    if r is None:
        return []
    lines = []
    for qid, q in r["questions"].items():
        parts = [accuracy_words(q["accuracy"], q["gold"])] if q.get("accuracy") else ["no answer key or reviews yet"]
        if w := act_words(q):
            parts.append(w)
        lines.append(f"{qid}: " + "; ".join(parts) + ".")
    lines += [metric_words(k, x) for k, x in (r.get("metrics") or {}).items() if x.get("rate") is not None]
    if ex := r.get("examples"):
        lines.append(f"Pinned examples: {sum(x['passed'] for x in ex)} of {len(ex)} give the expected answer.")
    return lines


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


def href(n: str, tab: str = "") -> str:
    return f"#/j/{quote(n, safe='')}" + (f"/{tab}" if tab else "")


def link(n: str) -> str:
    return f'<a href="{href(n)}" class="mono">{e(n)}</a>'


def text_of(v) -> str:
    return v if isinstance(v, str) else json.dumps(v, ensure_ascii=False, indent=1)


def pill(st: str) -> str:
    return f'<span class="pill st-{st}" title="{e(STATUS[st][1])}">{e(STATUS[st][0])}</span>'


def interval(a: dict) -> str:
    """Accuracy as what it is, a range: the track is 0–100%, the band the 95% interval, the dot the estimate."""
    v, ci = a["value"], a.get("ci")
    x = lambda f: 4 + f * 152  # 160 wide, 4 of margin each side so the dot never clips
    band = f'<rect class="band" x="{x(ci[0]):.1f}" y="3" width="{max(x(ci[1]) - x(ci[0]), 2):.1f}" height="8" rx="4"/>' if ci else ""
    return (f'<svg class="iv" viewBox="0 0 160 14" role="img" aria-label="{e(pct(v))}'
            + (f", 95% interval {e(pct(ci[0]))} to {e(pct(ci[1]))}" if ci else "") + '">'
            f'<rect class="track" x="4" y="5" width="152" height="4" rx="2"/>{band}'
            f'<line class="tick" x1="{x(.5)}" x2="{x(.5)}" y1="2" y2="12"/><circle class="pt" cx="{x(v):.1f}" cy="7" r="3.5"/></svg>')


def acc_cell(r: dict | None, sample: int | None) -> str:
    if not r:
        return '<span class="muted">–</span>'
    qs = [(qid, q) for qid, q in r["questions"].items() if q.get("accuracy")]
    if not qs:
        return '<span class="muted">not measured</span>'
    qid, q = min(qs, key=lambda x: x[1]["accuracy"]["value"])  # the weakest question is the one to know about
    a = q["accuracy"]
    ci = f" <small>{pct(a['ci'][0])}–{pct(a['ci'][1])}</small>" if a.get("ci") else ""
    more = f"lowest of {len(qs)} · " if len(qs) > 1 else ""
    return (f'<div class="acc"><span class="v">{pct(a["value"])}{ci}</span>{interval(a)}'
            f'<span class="muted" style="font-size:12px">{more}<span class="mono">{e(qid)}</span>'
            + (f" · sample of {sample}" if sample else "") + "</span></div>")


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


def sidebar(man: dict, st: dict) -> str:
    js = man["judgments"]
    counts = {k: sum(v == k for v in st.values()) for k in STATUS}
    chips = "".join(f'<button class="chip" type="button" data-st="{k}" aria-pressed="false"><span class="dot st-{k}"></span>'
                    f'{e(STATUS[k][0])} <span class="n">{c}</span></button>' for k, c in counts.items() if c)
    groups = []
    for k in STATUS:
        names = [n for n in ordered(js, st) if st[n] == k]
        if names:
            groups.append(f'<section><h4>{e(STATUS[k][0])}</h4>' + "".join(
                f'<a href="{href(n)}" data-name="{e(n)}" data-st="{k}" data-search="{e(search_text(n, js[n]))}">'
                f'<span class="dot st-{k}"></span>{e(n)}</a>' for n in names) + "</section>")
    return (f'<div class="chips" role="group" aria-label="Filter by status">{chips}</div>'
            f'<nav class="tree" aria-label="Judgments">{"".join(groups)}</nav>'
            '<nav class="side-links"><a href="#/">Overview</a><a href="#/lineage">Lineage</a></nav>')


def readme(project: dict) -> str:
    """The first paragraph of the folder's README, as plain text: the project's own words for the overview."""
    root = Path(project["path"])
    f = (root if root.is_dir() else root.parent) / "README.md"
    if not f.exists():
        return ""
    paras = [p.strip() for p in f.read_text().split("\n\n")]
    first = next((p for p in paras if p and not p.startswith(("#", "```", "|", "<", "!", "-", "*"))), "")
    return " ".join(first.split()).replace("**", "").replace("`", "")


def home(man: dict, res: dict, st: dict, run: dict, sample: int | None, about: str, at: str | None) -> str:
    js = man["judgments"]
    counts = {k: sum(v == k for v in st.values()) for k in STATUS}
    bar = "".join(f'<span class="st-{k}" style="flex:{c}" title="{c} {e(STATUS[k][0])}"></span>' for k, c in counts.items() if c)
    legend = "".join(f'<button type="button" data-st="{k}" aria-pressed="false"><span class="dot st-{k}"></span><b>{c}</b>'
                     f'<span class="lab">{e(STATUS[k][0])}</span></button>' for k, c in counts.items() if c)
    rows = []
    for n in ordered(js, st):
        m, r = js[n], res.get(n)
        acted = [q["act"] for q in (r or {}).get("questions", {}).values() if q.get("act")]
        auto = (f'{pct(min(a["automated"] for a in acted))}<div class="muted" style="font-size:12px">at its act</div>'
                if acted else '<span class="muted">–</span>')
        rows.append(
            f'<tr class="st-{st[n]}" data-st="{st[n]}" data-search="{e(search_text(n, m))}">'
            f'<td><a class="name" href="{href(n)}">{e(n)}</a><div style="margin-top:6px">{pill(st[n])}</div></td>'
            f'<td class="decides">{e(decides(m))}<div class="why"></div></td>'
            f'<td>{acc_cell(r, sample)}</td><td class="num hide-sm">{auto}</td>'
            f'<td class="hide-sm">{e(", ".join(x["name"] for x in m.get("exposures") or [])) or "<span class=muted>–</span>"}</td></tr>')
    tested = f"last test {e(local(at))}" + (f" · sample of {sample} rows" if sample else "") if at else "no test results for this path yet"
    return f"""<section id="home" class="home">
<p class="eyebrow">Project</p><h1>{e(man['project'])}</h1>
{f'<p class="lede">{e(about)}</p>' if about else ''}
<div class="meta"><span>{len(js)} judgment{'s' * (len(js) != 1)}</span><span>{tested}</span>{f"<span>git {e(man['git_sha'])}</span>" if man['git_sha'] else ''}</div>
<div class="health"><div class="bar" role="img" aria-label="{e(', '.join(f'{c} {STATUS[k][0]}' for k, c in counts.items() if c))}">{bar}</div>
<div class="legend" role="group" aria-label="Filter by status">{legend}</div></div>
<div class="table-wrap"><table class="inv"><thead><tr><th>Judgment</th><th>Decides</th><th>Accuracy</th><th class="hide-sm">Acts alone</th><th class="hide-sm">Used by</th></tr></thead>
<tbody>{''.join(rows)}</tbody></table><p class="empty" id="none" hidden>No judgment matches. Clear the search or the status filter.</p></div>
</section>"""


def attention(m: dict, r: dict | None, st: str, at: str | None, run: list[dict], sample: int | None) -> str:
    """Why the status is what it is, at the top of the page: the failing checks, or what makes the numbers old,
    partial or missing."""
    notes = []
    if st == "noresults":
        ran = f" It last ran {e(local(run[0].get('finished_at')))}." if run else ""
        notes.append(f"The last <code>hunch test</code> of this path did not include this judgment: never tested, tested alone "
                     f"with <code>--node</code> or through another path, or a test stopped by <code>--max-cost</code>.{ran}")
    if st == "stale":
        notes.append(f"The spec changed after its last test ({e(local(at))}): tested as <code>{e(r['spec_hash'])}</code>, now "
                     f"<code>{e(m['spec_hash'])}</code>. The numbers below describe the older version; run <code>hunch test</code>.")
    if sample and r:
        notes.append(f"Measured on a sample of {sample} rows (<code>--sample {sample}</code>), not on every row.")
    bad = [c for c in checks_of(r or {}) if not c["passed"]]

    def fmt(c: dict, k: str) -> str:
        v = c.get(k)
        return pct(v) if c["check"] in SHARE_CHECKS and isinstance(v, (int, float)) else e(v)
    items = "".join(f"<li><b>{e(c['on'])}</b> {e(c['check'])}"
                    + (f": {fmt(c, 'value')}, limit {fmt(c, 'limit')}" if c.get("value") is not None else "")
                    + ("" if c.get("severity", "error") == "error" else " (warn only)") + "</li>" for c in bad)
    if not notes and not items:
        return ""
    return (f'<div class="why-box st-{st}">' + "".join(f"<p>{x}</p>" for x in notes)
            + (f"<p>Failing at the last test:</p><ul>{items}</ul>" if items else "") + "</div>")


def figures(r: dict | None, sample: int | None) -> str:
    """One card per measured question: the estimate with its range, and what acting at `act` does."""
    if not r:
        return ""
    cards = []
    for qid, q in r["questions"].items():
        a = q.get("accuracy")
        if not a:
            continue
        basis = (f"estimated from {sum(v['reviewed'] for v in a.get('reviewed', {}).values())} reviewed rows"
                 if a["basis"] == "estimate" else f"agrees with the answer key on {q['gold']['rows']} rows")
        act = q.get("act")
        act_line = (f"at act {e(act['threshold'])}: acts alone on {pct(act['automated'])}"
                    + ("" if act["wrong"] is None else f", {pct(act['wrong'])} of those wrong") if act else "no act threshold")
        ci = f"<small>{pct(a['ci'][0])}–{pct(a['ci'][1])}</small>" if a.get("ci") else ""
        cards.append(f'<div class="fig"><span class="q">{e(qid)}</span><b>{pct(a["value"])}{ci}</b>{interval(a)}'
                     f'<span class="sub">{e(basis)}{" (sample)" if sample else ""}</span><span class="sub">{act_line}</span></div>')
    return f'<div class="figs">{"".join(cards)}</div>' if cards else ""


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


def question_block(qid: str, q: dict, r: dict | None) -> str:
    opts = options_of(q)
    parts = [f"<h3>{e(qid)}</h3><p class='qtext'>{e(text_of(q.get('instructions')))}</p>"]
    if opts:
        parts.append("<table class='data opts'>" + "".join(f"<tr><td><code>{e(k)}</code></td><td>{e(text_of(v))}</td></tr>"
                                                           for k, v in opts) + "</table>")
    esc = (q.get("escalate") or {}).get("model")
    meta = [f"type <code>{e(q.get('type'))}</code>"] + ([f"act <code>{e(q['act'])}</code>"] if "act" in q else []) \
        + ([f"gold column <code>{e(q['gold'])}</code>"] if q.get("gold") else []) \
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
        if r.get("checks"):
            parts.append("<details><summary>Checks</summary><table class='data num'><tr><th></th><th>check</th><th>value</th><th>limit</th></tr>"
                         + "".join(f"<tr><td>{'✓' if c['passed'] else '✗' if c.get('severity') == 'error' else '!'}</td>"
                                   f"<td><code>{e(c['check'])}</code></td><td>{e(c.get('value'))}</td><td>{e(c.get('limit'))}</td></tr>"
                                   for c in r["checks"]) + "</table></details>")
        mk = r.get("mistakes") or {}
        if mk.get("total"):
            parts.append(f"<details><summary>Most confident mistakes ({mk['total']})</summary><p class='note'>Wrong with high "
                         "confidence: a dangerous mistake, or a wrong answer key. <code>hunch review</code> shows them first.</p>"
                         "<table class='data'><tr><th>row</th><th>got</th><th>p</th><th>gold</th></tr>" + "".join(
                             f"<tr><td class='mono'>{e(x['id'])}</td><td>{e(x['got'])}</td><td class='num'>{e(x['p'])}</td>"
                             f"<td>{e(' | '.join(x['gold']))}</td></tr>" for x in mk["most_confident"]) + "</table></details>")
    return "<div>" + "".join(parts) + "</div>"


def judgment_page(n: str, m: dict, spec: dict, r: dict | None, st: str, run: list[dict], at: str | None, sample: int | None,
                  downstream: list[str], mini: str) -> str:
    reads = f"<code>{e(src[2])}</code>" if (src := source_node(m)) else " + ".join(link(u) for u in m["upstream"])
    exposures = "<br>".join(
        f"{e(x['name'])} <span class='muted'>{e(x.get('kind', 'app'))}{' · ' + e(x['owner']) if x.get('owner') else ''}"
        f"{' · reads ' + e(', '.join(x['uses'])) if x.get('uses') else ' · reads every answer'}</span>"
        + (f" <a href='{e(x['url'])}' rel='noopener'>open</a>" if str(x.get("url", "")).startswith(("https://", "http://")) else "")
        + (f"<br><span class='muted'>{e(x['description'])}</span>" if x.get("description") else "")
        for x in m.get("exposures") or [])
    facts = (f"<dt>Reads</dt><dd>{reads}" + (f" where <code>{e(m['where'])}</code>" if m.get("where") else "") + "</dd>"
             f"<dt>Model sees</dt><dd>{', '.join(f'<code>{e(c)}</code>' for c in m.get('state', [])) or '–'}"
             + (f"<br><span class='muted'>removed first: {e(', '.join(m['redact']))}</span>" if m.get("redact") else "") + "</dd>"
             f"<dt>Feeds</dt><dd>{', '.join(link(d) for d in downstream) or '–'}</dd>"
             f"<dt>Used by</dt><dd>{exposures or '–'}</dd>"
             f"<dt>Engine</dt><dd><code>{e(m.get('model'))}</code></dd>")
    sums = "".join(f"<li>{e(x)}</li>" for x in summary(r))
    union = (f"<p>Merges the answers to <code>{e(m.get('question'))}</code> from "
             f"{', '.join(link(u) for u in m['union'])}.</p>") if "union" in m else ""
    rmetrics = (r or {}).get("metrics") or {}
    metrics = "".join(f"<li><code>{e(k)}</code>: <code>{e((v or {}).get('rule'))}</code>"
                      + (f", fires on {pct(rmetrics[k]['rate'])}" if rmetrics.get(k, {}).get("rate") is not None else "") + "</li>"
                      for k, v in (m.get("metrics") or {}).items())
    examples = "".join(f"<li>{'✓' if x['passed'] else '✗'} {e(x['name'])}</li>" for x in (r or {}).get("examples") or [])
    runs_html = "".join(f"<tr><td>{e((x.get('finished_at') or '')[:16].replace('T', ' '))}</td><td>{e(x.get('rows'))}</td>"
                        f"<td>{e(x.get('asked'))}</td><td>${(x.get('cost') or 0):.4f}</td><td class='muted'>{e(x.get('status'))}</td></tr>"
                        for x in run)
    qs = "".join(question_block(qid, q, (r or {}).get("questions", {}).get(qid)) for qid, q in (m.get("questions") or {}).items())
    cols = "".join(f"<tr><td><code>{e(c)}</code></td><td>{e(w)}</td></tr>" for c, w in output_columns(spec))
    f = spec.get("_file")
    note, req = request_example(spec)
    tabs = [("overview", "Overview"), ("questions", "Questions"), ("output", "Output columns"), ("spec", "Spec"), ("request", "Request")]
    tested = f"tested {e(local(at))}" if r else "no test results"
    overview = f"""<div class="two"><div style="display:grid;gap:18px">
{f'<ul class="summary">{sums}</ul>' if sums else ''}<dl class="facts">{facts}</dl></div>
<div style="display:grid;gap:8px"><p class="eyebrow">Lineage</p><div class="dagbox mini">{mini}</div>
<a href="#/lineage/{quote(n, safe='')}" style="font-size:13px">Open in the full lineage</a></div></div>
{f'<div><h2>Metrics</h2><ul class="summary">{metrics}</ul></div>' if metrics else ''}
{f'<div><h2>Pinned examples</h2><ul class="summary">{examples}</ul></div>' if examples else ''}
{f'<div><h2>Recent runs</h2><div style="overflow-x:auto"><table class="data num"><tr><th>finished</th><th>rows</th><th>asked</th><th>cost</th><th>status</th></tr>{runs_html}</table></div></div>' if runs_html else ''}"""
    panes = {
        "overview": overview,
        "questions": union + (qs or "<p class='note'>No questions of its own.</p>"),
        "output": ("<p class='note'>The table this judgment writes to the store: what an app or a downstream judgment reads. "
                   f"Its name in the store is <code>{e(core.table_name(spec))}</code>.</p>"
                   f"<div style='overflow-x:auto'><table class='data'><tr><th>column</th><th>holds</th></tr>{cols}</table></div>"),
        "spec": (f"<p class='note'><code>{e(m['file'])}</code> · spec hash <code>{e(m['spec_hash'])}</code></p>"
                 f"<pre>{yaml_html(Path(f).read_text()) if f and Path(f).exists() else ''}</pre>"),
        "request": (f"<p class='note'>{e(note)} The row's fields are shown as placeholders; nothing from your data is on this page.</p>"
                    + (f"<pre>{e(req)}</pre>" if req else "")),
    }
    return f"""<section id="j-{e(n)}" hidden>
<p class="crumb"><a href="#/">Overview</a> / judgment</p>
<div class="jhead"><h1>{e(n)}</h1>{pill(st)}</div>
{f'<p class="desc">{e(m["description"])}</p>' if m.get("description") else ''}
<div class="meta"><span>{tested}</span><span>spec <code>{e(m['file'])}</code></span></div>
{attention(m, r, st, at, run, sample)}{figures(r, sample)}
<nav class="tabs" role="tablist">{''.join(f'<a role="tab" data-tab="{k}" href="{href(n, k)}" aria-selected="false">{label}</a>' for k, label in tabs)}</nav>
{''.join(f'<div class="pane" data-tab="{k}" role="tabpanel" hidden>{v}</div>' for k, v in panes.items())}
</section>"""


def page(project: dict, man: dict, results: dict | None, run: dict, st: dict[str, str]) -> str:
    res, at, sample = (results or {}).get("judgments", {}), (results or {}).get("at"), (results or {}).get("sample")
    js = man["judgments"]
    down = {n: [d for d, dm in js.items() if n in dm["upstream"]] for n in js}
    pages = "".join(judgment_page(n, m, project["nodes"][n], res.get(n), st[n], run.get(n, []), at, sample, down[n],
                                  lineage_svg(lineage(neighbourhood(man, n)), st, mini=True)) for n, m in js.items())
    lin = ('<section id="lineage" hidden><p class="eyebrow">Project</p><h1 style="font-size:34px">Lineage</h1>'
           '<p class="lede">Sources on the left, then judgments, then what uses them. Select a judgment to light up what it reads '
           'from and what depends on it; select it again to open it. Scroll to zoom, drag to move.</p>'
           '<div class="dagbox big" style="margin-top:18px"><div class="tools"><button type="button" data-z="in">Zoom in</button>'
           '<button type="button" data-z="out">Zoom out</button><button type="button" data-z="fit">Fit</button></div>'
           f'{lineage_svg(lineage(man), st)}</div></section>')
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
