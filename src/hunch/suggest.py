"""`hunch suggest`: rewrites of a question, proposed by a writer model and measured before you keep one."""

import asyncio
import json
import os
import random
import re
import sys
from pathlib import Path

import httpx
import yaml

from hunch import settings
from hunch.answers import attach_gold, decide, gold_str, hit, load_reviews, sign_test
from hunch.execute import execute, pick
from hunch.fill import endpoint, fill, llm_prices, llm_route, post
from hunch.spec import absolute_source, digest, item, label_of
from hunch.store import open_store, store_path

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
