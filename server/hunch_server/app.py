"""hunch server: online judgments, the review queue, runs and drift, over the same specs and store as the CLI.

Licensed under the Elastic License 2.0 (see ../LICENSE); the engine it serves (the `hunch` package) is Apache 2.0.

    HUNCH_PROJECTS=/path/to/specs  HUNCH_SERVER_TOKEN=secret  uvicorn hunch_server.app:app

Every path in a request is relative to HUNCH_PROJECTS and must stay inside it.
"""
import hmac
import html
import json
import os
import sqlite3
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlencode

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse, RedirectResponse
from starlette.routing import Route

from hunch import core

ROOT = Path(os.environ.get("HUNCH_PROJECTS", ".")).resolve()
TOKEN = os.environ.get("HUNCH_SERVER_TOKEN")
core.MAX_COST = float(os.environ.get("HUNCH_SERVER_MAX_COST", "0.01"))  # per judgment asked by one request
DRIFT_ALERT = 0.10  # total variation distance between two runs' label shares that is flagged


class Refused(Exception):
    def __init__(self, status: int, message: str):
        self.status, self.message = status, message


async def authorised(request: Request) -> None:
    if not TOKEN:
        return
    given = request.headers.get("authorization", "").removeprefix("Bearer ").strip() or request.query_params.get("token")
    if not given and request.headers.get("content-type", "").startswith("application/x-www-form-urlencoded"):
        given = (await request.form()).get("token")  # the review page's forms carry it (parsed once, cached)
    if not given or not hmac.compare_digest(given, TOKEN):
        raise Refused(401, "missing or wrong token (Authorization: Bearer …, ?token=… or a form field)")


def project_path(rel: str | None) -> Path:
    """A spec or folder under ROOT; anything resolving outside it (.., absolute paths, symlinks) is refused."""
    if not rel:
        raise Refused(400, "path is required (a spec file or folder, relative to the server's projects root)")
    p = (ROOT / rel).resolve()
    if p != ROOT and ROOT not in p.parents:
        raise Refused(403, f"{rel!r} is outside the projects root")
    if not p.exists():
        raise Refused(404, f"{rel!r} not found")
    return p


def load(rel: str) -> dict:
    try:
        project = core.load_project(project_path(rel))
    except SystemExit as e:  # the engine reports spec errors by exiting; a server must not
        raise Refused(422, str(e)) from None
    if not project["order"]:
        raise Refused(422, f"{rel!r} is not a spec or a folder of specs")
    return project


def audit_of(value) -> int:
    try:
        return int(value or 30)
    except ValueError:
        raise Refused(400, f"audit must be a whole number, got {value!r}") from None


def guarded(fn):
    async def wrapper(request: Request):
        try:
            await authorised(request)
            return await fn(request)
        except Refused as e:
            if request.url.path.startswith("/v1/"):
                return JSONResponse({"error": e.message}, status_code=e.status)
            return HTMLResponse(page("error", f"<p>{html.escape(e.message)}</p>"), status_code=e.status)
    return wrapper


def page(title: str, body: str) -> str:
    return f"""<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width">
<title>hunch · {html.escape(title)}</title><style>
body{{font:15px/1.45 system-ui,sans-serif;max-width:980px;margin:24px auto;padding:0 16px;color:#1d1d1f}}
table{{border-collapse:collapse;width:100%}}td,th{{border-bottom:1px solid #e5e5ea;padding:6px 8px;text-align:left;vertical-align:top}}
.card{{border:1px solid #e5e5ea;border-radius:8px;padding:12px 14px;margin:12px 0}}.k{{color:#6e6e73;font-size:13px}}
button{{margin:4px 6px 0 0;padding:5px 10px}}.warn{{color:#b3261e}}code{{background:#f5f5f7;padding:1px 4px;border-radius:4px}}
</style></head><body><p class="k"><a href="/">hunch</a> · {html.escape(title)}</p>{body}</body></html>"""


def specs_under_root() -> list[str]:
    folders = {p.parent for p in ROOT.rglob("*.yml") if ".hunch" not in p.parts and ".cache" not in p.parts}
    return sorted(str(f.relative_to(ROOT)) or "." for f in folders)


def runs_of(project: dict) -> list[dict]:
    db = core.open_store(project["nodes"][project["order"][0]])
    names = [core.table_name(s) for s in project["nodes"].values()]  # one store serves many projects
    try:
        cur = db.execute(f"select * from _hunch_runs where judgment in ({','.join('?' * len(names))}) "
                         "order by started_at desc limit 50", names)
    except sqlite3.OperationalError:
        return []
    cols = [c[0] for c in cur.description]
    return [dict(zip(cols, r)) for r in cur.fetchall()]


# ---------- JSON API ----------

@guarded
async def api_judge(request: Request):
    """{"path": spec, "row": {...}, "node"?: name, "shadow"?: candidate path, "log"?: bool} → the answers."""
    try:
        body = await request.json()
    except ValueError:
        raise Refused(400, "body must be JSON") from None
    if not isinstance(body, dict) or not isinstance(body.get("row", {}), dict):
        raise Refused(400, 'expected {"path": ..., "row": {column: value}}')
    path = project_path(body.get("path"))
    if not (path.is_file() or any(path.glob("*.yml"))):
        raise Refused(422, f"{body.get('path')!r} is not a spec or a folder of specs")
    shadow = project_path(body["shadow"]) if body.get("shadow") else None
    try:
        out = await core.ajudge(path, body.get("row") or {}, node=body.get("node"), shadow=shadow, log=bool(body.get("log")))
    except SystemExit as e:  # spec errors and the cost cap report by exiting
        raise Refused(422, str(e)) from None
    except KeyError as e:
        raise Refused(422, e.args[0] if e.args and isinstance(e.args[0], str) else f"row is missing {e}") from None
    return JSONResponse(out)


@guarded
async def api_runs(request: Request):
    return JSONResponse(runs_of(load(request.query_params.get("path"))))


def drift_of(project: dict, node: str) -> dict:
    """Label shares per run for each question, and the change from the previous run (total variation distance:
    half the sum of absolute share differences; 0 = same mix, 1 = disjoint)."""
    spec = project["nodes"][node]
    db = core.open_store(spec)
    try:
        got = db.execute("""select r.run_id, r.qid, a.answer from _hunch_row_answers r join answers a on a.key = r.key
                            where r.judgment = ? order by r.run_id""", (core.table_name(spec),)).fetchall()
    except sqlite3.OperationalError:
        return {}
    by: dict[str, dict[str, Counter]] = {}
    for run_id, qid, answer in got:
        by.setdefault(qid, {}).setdefault(run_id, Counter())[core.decide(json.loads(answer))[0]] += 1
    out = {}
    for qid, runs in by.items():
        series, prev = [], None
        for run_id, c in runs.items():
            n = sum(c.values())
            shares = {k: v / n for k, v in c.most_common()}
            tvd = None if prev is None else round(sum(abs(shares.get(k, 0) - prev.get(k, 0)) for k in set(shares) | set(prev)) / 2, 4)
            series.append({"run_id": run_id, "rows": n, "shares": {k: round(v, 4) for k, v in shares.items()},
                           "change": tvd, "alert": tvd is not None and tvd > DRIFT_ALERT})
            prev = shares
        out[qid] = series
    return out


@guarded
async def api_drift(request: Request):
    project = load(request.query_params.get("path"))
    return JSONResponse(drift_of(project, node_of(project, request.query_params.get("node"))))


# ---------- pages ----------

@guarded
async def home(request: Request):
    rows = []
    for rel in specs_under_root():
        try:
            project = core.load_project(ROOT / rel)
        except (SystemExit, Exception):  # a folder of other YAML is not a project
            continue
        runs = runs_of(project)
        last = runs[0] if runs else None
        q = urlencode({"path": rel, **({"token": request.query_params["token"]} if "token" in request.query_params else {})})
        rows.append(f"<tr><td><code>{html.escape(rel)}</code></td><td>{', '.join(map(html.escape, project['order']))}</td>"
                    f"<td>{html.escape(last['finished_at'] + ' · ' + last['status']) if last else '—'}</td>"
                    f"<td><a href='/review?{q}'>review</a> · <a href='/runs?{q}'>runs</a></td></tr>")
    return HTMLResponse(page("projects", f"<h1>Projects</h1><p class='k'>under {html.escape(str(ROOT))}</p>"
                             f"<table><tr><th>path</th><th>judgments</th><th>last run</th><th></th></tr>{''.join(rows)}</table>"))


@guarded
async def runs_page(request: Request):
    rel = request.query_params.get("path")
    project = load(rel)
    runs = runs_of(project)
    body = "".join(f"<tr><td>{html.escape(r['run_id'])}</td><td>{html.escape(r['judgment'])}</td><td>{r['rows']}</td>"
                   f"<td>{r['asked']}</td><td>${r['cost']:.4f}</td><td>{html.escape(r['model'])}</td>"
                   f"<td>{html.escape(r['spec_hash'])}</td><td>{html.escape(r['git_sha'] or '')}</td>"
                   f"<td>{html.escape(r['status'])}</td></tr>" for r in runs)
    drift = []
    for n in project["order"]:
        if "union" in project["nodes"][n]:
            continue
        for qid, series in drift_of(project, n).items():
            last = series[-1]
            flag = f" <span class='warn'>changed {last['change']:.0%} since the previous run</span>" if last["alert"] else ""
            shares = ", ".join(f"{html.escape(k)} {v:.0%}" for k, v in list(last["shares"].items())[:6])
            drift.append(f"<li><code>{html.escape(n)}.{html.escape(qid)}</code> ({len(series)} runs): {shares}{flag}</li>")
    return HTMLResponse(page(f"runs · {rel}", f"<h1>Runs</h1><table><tr><th>run</th><th>judgment</th><th>rows</th>"
                             f"<th>asked</th><th>cost</th><th>model</th><th>spec</th><th>git</th><th>status</th></tr>{body}</table>"
                             f"<h2>Label mix, latest run</h2><ul>{''.join(drift) or '<li>no runs yet</li>'}</ul>"))


async def queue_for(project: dict, node: str, audit: int = 30, with_answers: bool = False):
    """The review queue over answers already in the store (asks nothing)."""
    spec = project["nodes"][node]
    res = (await core.aexecute(project, dry=True))[node]
    items = [it for it in res["items"] if it["key"] in res["answers"]]
    core.attach_gold(items, core.load_reviews(spec))
    queue = core.review_queue(items, res["answers"], audit)
    return (queue, res["answers"]) if with_answers else queue


def node_of(project: dict, name: str | None) -> str:
    try:
        return core.pick(project, name)
    except SystemExit as e:
        raise Refused(400, str(e)) from None


@guarded
async def review_page(request: Request):
    rel = request.query_params.get("path")
    project = load(rel)
    node = node_of(project, request.query_params.get("node"))
    spec = project["nodes"][node]
    audit = audit_of(request.query_params.get("audit"))
    queue, answers = await queue_for(project, node, audit, with_answers=True)
    kinds = Counter(k for k, _ in queue)
    token, reviewer = request.query_params.get("token", ""), request.query_params.get("reviewer", "")
    cards = []
    for kind, it in queue[:25]:
        a = answers[it["key"]]
        top = core.ranked(a)[:3]
        key = core.review_key(it, a)  # a spot check without an answer key confirms the model's answer
        state = "".join(f"<div><span class='k'>{html.escape(c)}</span><br>{html.escape(str(v))[:1500]}</div>"
                        for c, v in core.state_parts(spec, it["state"]).items())
        choices = {"disputed": [("model_right", top[0][0], "model is right"), ("key_right", key, "answer key is right"),
                                ("both_ok", f"{key}|{top[0][0]}", "both acceptable")],
                   "audit": [("confirmed", key, "label is right")]}.get(kind, [])
        choices += [("labeled", lab, f"it is {lab}") for lab, _ in top if lab not in {c[1] for c in choices}]
        choices += [("needs_context", "", "needs more context"), ("ambiguous", "", "ambiguous")]
        hidden = {"path": rel, "node": node, "qid": it["qid"], "row_id": it["id"], "state_hash": it["shash"],
                  "kind": kind, "token": token, "reviewer": reviewer, "audit": audit}
        buttons = "".join(
            "<form method='post' action='/review' style='display:inline'>"
            + "".join(f"<input type='hidden' name='{k}' value='{html.escape(str(v))}'>" for k, v in hidden.items())
            + f"<input type='hidden' name='verdict' value='{v}'><input type='hidden' name='label' value='{html.escape(lab)}'>"
            f"<button>{html.escape(text)}</button></form>" for v, lab, text in choices)
        cards.append(f"<div class='card'><p class='k'>{kind} · #{html.escape(it['id'])} · {html.escape(it['qid'])}"
                     f"{' · answer key: ' + html.escape(key) if it['raw_gold'] else ''}</p>{state}"
                     f"<p>model: {' · '.join(f'{html.escape(l)} {p:.2f}' for l, p in top)}</p>"
                     f"{buttons}</div>")
    head = (f"<h1>Review · {html.escape(node)}</h1><p>{kinds['disputed']} disputed · {kinds['audit']} audit · "
            f"{kinds['uncertain']} uncertain; verdicts go to <code>{html.escape(core.reviews_path(spec).name)}</code>"
            f"{' as ' + html.escape(reviewer) if reviewer else ' (add &amp;reviewer=you to the address to sign them)'}</p>"
            "<p>Judge only from the text shown, as a stranger would. If you can only decide because you know more "
            "than this, choose <b>needs more context</b>: it counts as missing context, not a model error.</p>")
    return HTMLResponse(page(f"review · {rel}", head + ("".join(cards) or "<p>Nothing to review.</p>")))


@guarded
async def review_post(request: Request):
    form = await request.form()
    rel = form.get("path")
    project = load(rel)
    node = node_of(project, form.get("node"))
    spec = project["nodes"][node]
    if form.get("verdict") not in {"model_right", "key_right", "both_ok", "confirmed", "labeled", "ambiguous",
                                "needs_context"}:
        raise Refused(400, f"unknown verdict {form.get('verdict')!r}")
    # only rows the queue offers, with the kind it offers them as: `audit` must stay a random sample for the
    # estimator, so a verdict can't be filed as one for a row picked by hand
    queue = await queue_for(project, node, audit_of(form.get("audit")))  # the same queue the page offered
    offered = {(it["qid"], it["id"], it["shash"]): kind for kind, it in queue}
    if offered.get((form.get("qid"), form.get("row_id"), form.get("state_hash"))) != form.get("kind"):
        raise Refused(409, "that row is not in the review queue as that kind (already reviewed, or changed)")
    core.append_review(spec, {"qid": form["qid"], "row_id": form["row_id"], "state_hash": form["state_hash"],
                              "kind": form["kind"], "verdict": form["verdict"], "label": form.get("label", ""),
                              "reviewer": request.headers.get("x-reviewer") or form.get("reviewer") or "server",
                              "at": datetime.now(UTC).isoformat(timespec="seconds")})
    q = urlencode({"path": rel, "node": node, **{k: form[k] for k in ("token", "reviewer", "audit") if form.get(k)}})
    return RedirectResponse(f"/review?{q}", status_code=303)


app = Starlette(routes=[
    Route("/", home), Route("/runs", runs_page), Route("/review", review_page), Route("/review", review_post, methods=["POST"]),
    Route("/v1/judge", api_judge, methods=["POST"]), Route("/v1/runs", api_runs), Route("/v1/drift", api_drift),
])
