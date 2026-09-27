"""`hunch test`: accuracy, calibration, recall, metrics, baselines and examples, and the results file."""

import asyncio
import json
import os
import sys
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

from hunch import settings
from hunch.answers import (
    accuracy,
    act_needed,
    attach_gold,
    auroc,
    calib_pairs,
    calibration,
    conf_of,
    decide,
    estimate_accuracy,
    gold_str,
    hit,
    is_yes,
    load_reviews,
    newcombe,
    normalize_gold,
    permuted,
    route,
    sample,
    show,
    wilson,
    wrate,
)
from hunch.execute import execute, print_stats
from hunch.fill import fill, merge_stats
from hunch.spec import (
    DIAL,
    FEW,
    NOISE,
    NONE,
    SHOW,
    TEST_KEYS,
    Unknown,
    asked,
    compile_baseline,
    compile_where,
    detail,
    item,
    label_of,
    question_of,
)
from hunch.store import git_sha, open_store, receipt_path, results_path, spec_hash, store_path


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
