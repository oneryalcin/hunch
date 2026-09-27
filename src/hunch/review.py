"""`hunch review`: the rows whose verdicts teach the most, and recording verdicts."""

import getpass
import os
import shutil
import sys
import textwrap
from collections import Counter
from datetime import UTC, datetime

from hunch.answers import (
    append_review,
    attach_gold,
    conf_of,
    decide,
    gold_str,
    hit,
    load_reviews,
    ranked,
    reviews_path,
    route,
    show,
)
from hunch.diff import load_against, twin_of
from hunch.execute import execute, pick
from hunch.spec import digest, label_of, state_columns


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
