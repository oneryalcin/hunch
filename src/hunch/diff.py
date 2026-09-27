"""`hunch diff`: which answers a changed spec would flip, and what relies on them."""

import sys

from hunch import settings
from hunch.answers import accuracy, attach_gold, decide, hit, load_reviews, ranked, show, sign_test
from hunch.execute import execute, print_stats
from hunch.fill import merge_stats
from hunch.lint import apply_target, relies_on, uses_of, uses_text
from hunch.measure import ungrade
from hunch.online import roots
from hunch.spec import (
    NOISE,
    SHOW,
    absolute_source,
    detail,
    label_of,
    load_project_ref,
    question_of,
    source_header,
    state_columns,
)
from hunch.store import spec_hash


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
