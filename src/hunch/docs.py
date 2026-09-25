"""`hunch docs`: the project as one page people can read and search, and as manifest.json for tools.

Asks nothing and costs nothing. It reads the specs, the last `hunch test` of the same path (results.json) and the
store's run log, and shows only numbers `test` produced. A judgment whose spec changed after that test is marked
stale rather than shown with numbers that describe an older version.
"""
import html
import json
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
    if m["upstream"]:
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


def lineage_svg(lin: dict, st: dict[str, str]) -> str:
    W, H, BW, BH = 230, 64, 180, 40
    size = {c: sum(nd["col"] == c for nd in lin["nodes"]) for c in {nd["col"] for nd in lin["nodes"]}}
    tall = max(size.values(), default=1)  # short columns are centred against the tallest
    pos = {nd["id"]: (20 + nd["col"] * W, 20 + (nd["row"] + (tall - size[nd["col"]]) / 2) * H) for nd in lin["nodes"]}
    width = max((x for x, _ in pos.values()), default=0) + BW + 40
    height = max((y for _, y in pos.values()), default=0) + BH + 40
    out = [f'<svg class="dag" viewBox="0 0 {width} {height}" width="{width}" height="{height}" role="img" aria-label="lineage">']
    for a, b in lin["edges"]:
        (x1, y1), (x2, y2) = pos[a], pos[b]
        x1, y1, y2 = x1 + BW, y1 + BH / 2, y2 + BH / 2
        mid = (x1 + x2) / 2
        out.append(f'<path class="edge" data-a="{e(a)}" data-b="{e(b)}" d="M{x1},{y1} C{mid},{y1} {mid},{y2} {x2},{y2}"/>')
    for nd in lin["nodes"]:
        x, y = pos[nd["id"]]
        label = nd["label"] if len(nd["label"]) <= 24 else nd["label"][:23] + "…"
        cls = f'node {nd["kind"]}' + (f' st-{st[nd["id"]]}' if nd["kind"] == "judgment" else "")
        sub = {"source": "source", "exposure": nd.get("sub", "app")}.get(nd["kind"]) or STATUS[st[nd["id"]]][0]
        out.append(f'<g class="{cls}" data-id="{e(nd["id"])}" transform="translate({x},{y})">'
                   f'<title>{e(nd.get("title", nd["label"]))}</title><rect width="{BW}" height="{BH}" rx="6"/>'
                   f'<text x="10" y="17">{e(label)}</text><text class="sub" x="10" y="32">{e(sub)}</text></g>')
    return "\n".join(out + ["</svg>"])


# ---------- page ----------

def e(x) -> str:
    return html.escape("" if x is None else str(x))


def link(n: str) -> str:
    return f'<a href="#/j/{quote(n, safe="")}">{e(n)}</a>'


def text_of(v) -> str:
    return v if isinstance(v, str) else json.dumps(v, ensure_ascii=False, indent=1)


def badge(st: str) -> str:
    return f'<span class="badge st-{st}" title="{e(STATUS[st][1])}">{e(STATUS[st][0])}</span>'


def measured_cell(r: dict | None, sample: int | None) -> str:
    if not r:
        return "–"
    bits = [f'{e(qid)} {pct(q["accuracy"]["value"])}' + (f' <span class="dim">({pct(q["accuracy"]["ci"][0])}–{pct(q["accuracy"]["ci"][1])})</span>'
            if q["accuracy"].get("ci") else "") for qid, q in r["questions"].items() if q.get("accuracy")]
    rest = len(r["questions"]) - len(bits)
    out = "<br>".join(bits[:2]) + (f'<br><span class="dim">+{len(bits) - 2} more</span>' if len(bits) > 2 else "")
    out += (f'<br><span class="dim">{rest} not measured</span>' if rest and bits else "" if bits else '<span class="dim">not measured</span>')
    return out + (f'<br><span class="dim">on a sample of {sample} rows</span>' if sample and bits else "")


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


def search_text(n: str, m: dict) -> dict:
    qs = m.get("questions") or {}
    return {"name": n, "description": m.get("description") or "",
            "questions": " ".join(f"{qid} {text_of(q.get('instructions'))}" for qid, q in qs.items()),
            "options": " ".join(f"{k} {text_of(v)}" for q in qs.values() for k, v in options_of(q)),
            "columns": " ".join([*m.get("state", []), m.get("key") or ""]), "source": str(m.get("source", "")),
            "used by": " ".join(f"{x['name']} {x.get('kind', '')} {x.get('owner', '')} {x.get('description', '')}"
                                for x in m.get("exposures") or [])}


def inventory(man: dict, res: dict, st: dict, run: dict, sample: int | None) -> str:
    js = man["judgments"]
    counts = {k: sum(v == k for v in st.values()) for k in STATUS}
    head = " · ".join(f"{c} {STATUS[k][0]}" for k, c in counts.items() if c)
    rows = []
    for n, m in sorted(js.items(), key=lambda kv: (list(STATUS).index(st[kv[0]]), kv[0])):
        last = (run.get(n) or [{}])[0]
        rows.append(
            f'<tr data-search="{e(json.dumps(search_text(n, m)))}"><td>{link(n)}</td>'
            f'<td>{e(decides(m))}<div class="why"></div></td><td>{badge(st[n])}</td><td>{measured_cell(res.get(n), sample)}</td>'
            f'<td class="dim">{e((last.get("finished_at") or "")[:10]) or "–"}</td>'
            f'<td>{e(", ".join(x["name"] for x in m.get("exposures") or [])) or "–"}</td></tr>')
    return f"""<section id="v-home">
<p class="lede">{len(js)} judgment{'s' * (len(js) != 1)} · {head}</p>
<table class="inv"><thead><tr><th>judgment</th><th>decides</th><th>status</th><th>measured</th><th>last run</th><th>used by</th></tr></thead>
<tbody>{''.join(rows)}</tbody></table>
<p class="none-found" hidden>Nothing matches.</p>
<dl class="legend">{''.join(f'<dt>{badge(k)}</dt><dd>{e(v[1])}</dd>' for k, v in STATUS.items() if counts[k])}</dl>
</section>"""


def attention(m: dict, r: dict | None, st: str, at: str | None, run: list[dict], sample: int | None) -> str:
    """Why the status is what it is, at the top of the page: the failing checks, or what makes the numbers old,
    partial or missing."""
    notes = []
    if st == "noresults":
        ran = f" It last ran {e(local(run[0].get('finished_at')))}." if run else ""
        notes.append(f"The last <code>hunch test</code> of this path did not include this judgment (never tested, "
                     f"tested with <code>--node</code>, tested through another path, or stopped by <code>--max-cost</code>).{ran}")
    if st == "stale":
        notes.append(f"The spec changed after its last test ({e(local(at))}): tested as <code>{e(r['spec_hash'])}</code>, "
                     f"now <code>{e(m['spec_hash'])}</code>. The numbers below describe the older version.")
    if sample and r:
        notes.append(f"Measured on a sample of {sample} rows (<code>--sample {sample}</code>), not every row.")
    bad = [c for c in checks_of(r or {}) if not c["passed"]]

    def fmt(c: dict, k: str) -> str:
        v = c.get(k)
        return pct(v) if c["check"] in SHARE_CHECKS and isinstance(v, (int, float)) else e(v)
    items = "".join(f"<li>{'✗' if c.get('severity', 'error') == 'error' else '!'} <b>{e(c['on'])}</b> {e(c['check'])}"
                    + (f": {fmt(c, 'value')}, limit {fmt(c, 'limit')}" if c.get("value") is not None else "")
                    + ("" if c.get("severity", "error") == "error" else " <span class='dim'>(warn only)</span>") + "</li>"
                    for c in bad)
    return "".join(f"<p class='why-box'>{x}</p>" for x in notes) + (f"<ul class='why-box st-{st}'>{items}</ul>" if items else "")


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
    return ("<p class='dim'>Counted over the rows with a known answer, so the shares can differ from the summary's, which "
            f"counts every row. Pick <code>act</code> where the wrong share is one you can live with.</p><table class='num'>{head}{body}</table>")


def question_block(qid: str, q: dict, r: dict | None) -> str:
    opts = options_of(q)
    parts = [f"<p class='q'>{e(text_of(q.get('instructions')))}</p>"]
    if opts:
        parts.append("<table class='opts'>" + "".join(f"<tr><td><code>{e(k)}</code></td><td>{e(text_of(v))}</td></tr>"
                                                      for k, v in opts) + "</table>")
    esc = (q.get("escalate") or {}).get("model")
    meta = [f"type <code>{e(q.get('type'))}</code>"] + ([f"act <code>{e(q['act'])}</code>"] if "act" in q else []) \
        + ([f"gold column <code>{e(q['gold'])}</code>"] if q.get("gold") else []) \
        + ([f"uncertain answers re-asked of <code>{e(esc)}</code>"] if esc else [])
    parts.append(f"<p class='dim'>{' · '.join(meta)}</p>")
    if r:
        if r.get("dial"):
            parts.append(f"<details><summary>Where to set act (the dial)</summary>{dial_table(r)}</details>")
        cal = ([f"calibration error {r['calibration_error']:.3f} (0 = stated confidence matches how often it is right)"]
               if r.get("calibration_error") is not None else [])
        cal += [f"AUROC {r['auroc']:.3f} (how well p(yes) separates yes from no; 0.5 = coin toss)"] if r.get("auroc") is not None else []
        if cal:
            parts.append(f"<details><summary>Calibration</summary><p>{'<br>'.join(e(c) for c in cal)}</p></details>")
        if r.get("checks"):
            parts.append("<details><summary>Checks</summary><table class='num'>" + "".join(
                f"<tr><td>{'✓' if c['passed'] else '✗' if c.get('severity') == 'error' else '!'}</td><td>{e(c['check'])}</td>"
                f"<td>{e(c.get('value'))}</td><td class='dim'>limit {e(c.get('limit'))}</td></tr>" for c in r["checks"]) + "</table></details>")
        mk = r.get("mistakes") or {}
        if mk.get("total"):
            parts.append(f"<details><summary>Most confident mistakes ({mk['total']})</summary><p class='dim'>Wrong with high "
                         "confidence: a dangerous mistake, or a wrong answer key. Review them with <code>hunch review</code>.</p>"
                         "<table class='num'><tr><th>row</th><th>got</th><th>p</th><th>gold</th></tr>" + "".join(
                             f"<tr><td>{e(x['id'])}</td><td>{e(x['got'])}</td><td>{e(x['p'])}</td><td>{e(' | '.join(x['gold']))}</td></tr>"
                             for x in mk["most_confident"]) + "</table></details>")
    return f"<h3>{e(qid)}</h3>" + "".join(parts)


def judgment_page(n: str, m: dict, r: dict | None, st: str, run: list[dict], at: str | None, sample: int | None,
                  downstream: list[str]) -> str:
    if src := source_node(m):
        reads = f"<code>{e(src[2])}</code>"
    else:
        reads = " + ".join(link(u) for u in m["upstream"])
    exposures = "<br>".join(
        f"{e(x['name'])} <span class='dim'>{e(x.get('kind', 'app'))}{' · ' + e(x['owner']) if x.get('owner') else ''}"
        f"{' · reads ' + e(', '.join(x['uses'])) if x.get('uses') else ''}</span>"
        + (f" <a href='{e(x['url'])}'>↗</a>" if str(x.get("url", "")).startswith(("https://", "http://")) else "")
        + (f"<br><span class='dim'>{e(x['description'])}</span>" if x.get("description") else "")
        for x in m.get("exposures") or [])
    facts = [f"<dt>Reads</dt><dd>{reads}" + (f" where <code>{e(m['where'])}</code>" if m.get("where") else "") + "</dd>",
             f"<dt>Model sees</dt><dd>{', '.join(f'<code>{e(c)}</code>' for c in m.get('state', [])) or '–'}"
             + (f"<br><span class='dim'>removed first: {e(', '.join(m['redact']))}</span>" if m.get("redact") else "") + "</dd>",
             f"<dt>Feeds</dt><dd>{', '.join(link(d) for d in downstream) or '–'}</dd>",
             f"<dt>Used by</dt><dd>{exposures or '–'}</dd>"]
    tested = f"last tested {e(local(at))}" if r else "no test results"
    sums = "".join(f"<li>{e(x)}</li>" for x in summary(r))
    qs = "".join(question_block(qid, q, (r or {}).get("questions", {}).get(qid)) for qid, q in (m.get("questions") or {}).items())
    union = (f"<p>Merges the answers to <code>{e(m.get('question'))}</code> from "
             f"{', '.join(link(u) for u in m['union'])}.</p>") if "union" in m else ""
    rmetrics = (r or {}).get("metrics") or {}
    metrics = "".join(f"<li><code>{e(k)}</code>: <code>{e((v or {}).get('rule'))}</code>"
                      + (f" fires on {pct(rmetrics[k]['rate'])}" if rmetrics.get(k, {}).get("rate") is not None else "") + "</li>"
                      for k, v in (m.get("metrics") or {}).items())
    examples = "".join(f"<li>{'✓' if x['passed'] else '✗'} {e(x['name'])}</li>" for x in (r or {}).get("examples") or [])
    runs_html = "".join(f"<tr><td>{e((x.get('finished_at') or '')[:16].replace('T', ' '))}</td><td>{e(x.get('rows'))}</td>"
                        f"<td>{e(x.get('asked'))}</td><td>${(x.get('cost') or 0):.4f}</td><td class='dim'>{e(x.get('status'))}</td></tr>"
                        for x in run)
    return f"""<section id="v-j-{e(n)}" class="page" hidden>
<p class="crumb"><a href="#/">all judgments</a></p>
<h1>{e(n)} {badge(st)}</h1>
<p class="dim">{tested} · model <code>{e(m.get('model'))}</code> · spec <code>{e(m['file'])}</code></p>
{attention(m, r, st, at, run, sample)}
{f'<p class="desc">{e(m["description"])}</p>' if m.get("description") else ''}
{f'<ul class="summary">{sums}</ul>' if sums else ''}
<dl class="facts">{''.join(facts)}</dl>
<p><a href="#/lineage/{quote(n, safe='')}">Show in lineage →</a></p>
<h2>Questions</h2>{union}{qs}
{f'<h2>Metrics</h2><ul>{metrics}</ul>' if metrics else ''}
{f'<h2>Pinned examples</h2><ul>{examples}</ul>' if examples else ''}
{f'<h2>Recent runs</h2><table class="num"><tr><th>finished</th><th>rows</th><th>asked</th><th>cost</th><th>status</th></tr>{runs_html}</table>' if runs_html else ''}
</section>"""


def page(man: dict, results: dict | None, run: dict, st: dict[str, str]) -> str:
    res, at, sample = (results or {}).get("judgments", {}), (results or {}).get("at"), (results or {}).get("sample")
    js = man["judgments"]
    down = {n: [d for d, dm in js.items() if n in dm["upstream"]] for n in js}
    pages = "".join(judgment_page(n, m, res.get(n), st[n], run.get(n, []), at, sample, down[n]) for n, m in js.items())
    body = (inventory(man, res, st, run, sample) + pages
            + '<section id="v-lineage" hidden><p class="crumb"><a href="#/">all judgments</a></p><h1>Lineage</h1>'
              '<p class="dim">Select a judgment to light up what feeds it and what it affects; select it again to open it.</p>'
              f'<div class="dagwrap">{lineage_svg(lineage(man), st)}</div></section>')
    made = datetime.now().astimezone().strftime("%Y-%m-%d %H:%M") + (f" · git {e(man['git_sha'])}" if man["git_sha"] else "")
    return (TEMPLATE.replace("{{title}}", e(man["project"])).replace("{{css}}", CSS).replace("{{js}}", JS)
            .replace("{{body}}", body).replace("{{generated}}", made))


def write_docs(project: dict) -> None:
    rp = core.results_path(project)
    results = json.loads(rp.read_text()) if rp.exists() else None
    man = manifest(project)
    st = {n: status(m, (results or {}).get("judgments", {}).get(n)) for n, m in man["judgments"].items()}
    mpath, hpath = rp.with_suffix(".manifest.json"), rp.with_suffix(".html")
    mpath.parent.mkdir(parents=True, exist_ok=True)
    mpath.write_text(json.dumps(man, indent=1, ensure_ascii=False, default=str) + "\n")
    hpath.write_text(page(man, results, runs(project), st))
    counts = [f"{list(st.values()).count(k)} {v[0]}" for k, v in STATUS.items() if k in st.values()]
    print(f"{len(st)} judgment{'s' * (len(st) != 1)}: " + ", ".join(counts))
    print(f"  {hpath}\n  {mpath}")


TEMPLATE = """<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>{{title}} · hunch</title><style>{{css}}</style></head><body>
<header><a class="brand" href="#/">{{title}}</a><nav><a href="#/">Judgments</a><a href="#/lineage">Lineage</a></nav>
<input id="q" type="search" placeholder="Search judgments, questions, options, columns, apps" aria-label="search"></header>
<main>{{body}}</main><footer>Generated by hunch docs · {{generated}} · numbers from the last hunch test</footer>
<script>{{js}}</script></body></html>"""

CSS = """:root{--bg:#fff;--fg:#252522;--dim:#6b6b66;--line:#e6e4de;--accent:#405A60;--card:#fafaf8;
--ok:#3F7D58;--fail:#C64D35;--warn:#A86B12;--stale:#A86B12;--noresults:#8a8a85;--nogold:#7D969B}
@media (prefers-color-scheme: dark){:root{--bg:#0f1012;--fg:#e9e6de;--dim:#9a988f;--line:#2a2b2e;--accent:#7D969B;--card:#16171a}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--fg);font:15px/1.55 system-ui,-apple-system,Segoe UI,sans-serif}
header{position:sticky;top:0;background:var(--bg);border-bottom:1px solid var(--line);display:flex;gap:20px;align-items:center;padding:10px 24px;flex-wrap:wrap;z-index:1}
.brand{font-weight:600;color:var(--fg);text-decoration:none}nav a{margin-right:14px}
#q{flex:1;min-width:220px;max-width:460px;padding:7px 10px;border:1px solid var(--line);border-radius:6px;background:var(--card);color:var(--fg);font:inherit}
main{max-width:1100px;margin:0 auto;padding:24px}footer{color:var(--dim);font-size:13px;text-align:center;padding:24px}
a{color:var(--accent)}code{font:13px ui-monospace,Menlo,monospace;background:var(--card);padding:1px 4px;border-radius:4px}
h1{font-size:26px;margin:.2em 0}h2{font-size:18px;margin-top:2em;border-bottom:1px solid var(--line);padding-bottom:4px}h3{font-size:16px;margin:1.6em 0 .3em}
.dim,.crumb{color:var(--dim)}.why-box{border-left:3px solid currentColor;padding:8px 14px;margin:12px 0;background:var(--card);list-style:none}
.why-box li{margin:2px 0;color:var(--fg)}p.why-box{color:var(--fg);border-left-color:var(--warn)}ul.why-box.st-fail{color:var(--fail)}.lede{font-size:16px}.desc{font-size:17px}
table{border-collapse:collapse;width:100%}th,td{text-align:left;padding:8px 10px;border-bottom:1px solid var(--line);vertical-align:top}
th{font-size:13px;color:var(--dim);font-weight:500}.num{width:auto}.num td,.num th{padding:4px 12px 4px 0}tr.cur td{font-weight:600}
.inv td:first-child{white-space:nowrap;font-weight:500}.why{font-size:12px;color:var(--dim)}
.badge{display:inline-block;font-size:12px;padding:1px 8px;border-radius:10px;border:1px solid currentColor;white-space:nowrap;vertical-align:middle}
.st-ok{color:var(--ok)}.st-fail{color:var(--fail)}.st-warn,.st-stale{color:var(--warn)}.st-noresults{color:var(--noresults)}.st-nogold{color:var(--nogold)}
.legend{display:grid;grid-template-columns:max-content 1fr;gap:6px 12px;font-size:13px;color:var(--dim);margin-top:20px}.legend dd{margin:0}
.inv td:nth-child(5){white-space:nowrap}.summary{font-size:16px;padding-left:20px}.summary li{margin:.3em 0}
.facts{display:grid;grid-template-columns:120px 1fr;gap:6px 16px;background:var(--card);border:1px solid var(--line);border-radius:8px;padding:14px 18px}
.facts dt{color:var(--dim)}.facts dd{margin:0}.q{font-size:16px;white-space:pre-wrap}.opts td:first-child{width:1%;white-space:nowrap}
details{margin:.4em 0}summary{cursor:pointer;color:var(--accent)}
.dagwrap{overflow:auto;border:1px solid var(--line);border-radius:8px;background:var(--card)}
.dag .edge{fill:none;stroke:var(--dim);stroke-opacity:.5;stroke-width:1.5}.dag .node rect{fill:var(--bg);stroke:var(--line);stroke-width:1.5}
.dag .node text{fill:var(--fg);font-size:13px}.dag .node .sub{fill:var(--dim);font-size:11px}
.dag .judgment{cursor:pointer}.dag .judgment rect{stroke:currentColor}.dag .source rect{stroke-dasharray:4 3}.dag .exposure rect{fill:var(--card)}
.dag.focus .node,.dag.focus .edge{opacity:.18}.dag.focus .on{opacity:1}.dag.focus .edge.on{stroke:var(--accent);stroke-opacity:1}
.dag .sel rect{stroke-width:3}
@media (max-width:700px){main{padding:16px}.inv th:nth-child(5),.inv td:nth-child(5),.inv th:nth-child(6),.inv td:nth-child(6){display:none}.facts{grid-template-columns:1fr}}"""

JS = r"""const $=(s,r=document)=>r.querySelector(s),$$=(s,r=document)=>[...r.querySelectorAll(s)];
function show(){const h=location.hash.slice(1)||'/';$$('main>section').forEach(s=>s.hidden=true);
 let m;if(m=h.match(/^\/j\/(.+)$/)){const s=document.getElementById('v-j-'+decodeURIComponent(m[1]));if(s){s.hidden=false;scrollTo(0,0);return}}
 if(m=h.match(/^\/lineage(?:\/(.+))?$/)){$('#v-lineage').hidden=false;focus(m[1]?decodeURIComponent(m[1]):null);return}
 $('#v-home').hidden=false}
const dag=$('.dag'),edges=$$('.edge',dag||document);
function reach(id,dir){const seen=new Set([id]),todo=[id];while(todo.length){const n=todo.pop();
 for(const e of edges){const [a,b]=dir>0?[e.dataset.a,e.dataset.b]:[e.dataset.b,e.dataset.a];if(a===n&&!seen.has(b)){seen.add(b);todo.push(b)}}}return seen}
function focus(id){if(!dag)return;$$('.on,.sel',dag).forEach(x=>x.classList.remove('on','sel'));dag.classList.toggle('focus',!!id);if(!id)return;
 const on=new Set([...reach(id,1),...reach(id,-1)]);$$('.node',dag).forEach(n=>{if(on.has(n.dataset.id))n.classList.add('on');if(n.dataset.id===id)n.classList.add('sel')});
 edges.forEach(e=>{if(on.has(e.dataset.a)&&on.has(e.dataset.b))e.classList.add('on')})}
$$('.node',dag||document).forEach(n=>n.addEventListener('click',()=>{const id=n.dataset.id;
 if(n.classList.contains('judgment')&&n.classList.contains('sel'))location.hash='/j/'+encodeURIComponent(id);else location.hash='/lineage/'+encodeURIComponent(id)}));
$('#q').addEventListener('input',ev=>{const q=ev.target.value.trim().toLowerCase();if(q&&location.hash!==''&&location.hash!=='#/')location.hash='/';let any=false;
 $$('.inv tbody tr').forEach(tr=>{const f=JSON.parse(tr.dataset.search);const hit=Object.entries(f).find(([k,v])=>v.toLowerCase().includes(q));
  tr.hidden=!!q&&!hit;any=any||!tr.hidden;$('.why',tr).textContent=q&&hit&&hit[0]!=='name'?'matches '+hit[0]:''});$('.none-found').hidden=any});
addEventListener('hashchange',show);show();"""
