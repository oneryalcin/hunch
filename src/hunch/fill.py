"""Asking engines: prices and cost caps, prompts for LLM endpoints, requests, and filling missing answers."""

import asyncio
import json
import math
import os
import sys
import time

import httpx

from hunch import engines, settings
from hunch.spec import ENDPOINTS, OPENROUTER, is_llm, state_parts
from hunch.store import cached, write

API = "https://api.typesafe.ai/v1/systemone"


PRICE_PER_INPUT_TOKEN = 0.042 / 1_000_000  # output tokens are free


REQUEST_OVERHEAD_TOKENS = 275  # measured on jev-1.13.0: fixed input tokens per request beyond ~chars/4


CONCURRENCY = int(os.environ.get("HUNCH_CONCURRENCY", 16))  # requests in flight per fill


engines.RESERVED |= set(ENDPOINTS) | {"distilled"}


LLM_OVERHEAD_TOKENS = 40  # prompt scaffolding per request beyond ~chars/4


LLM_MAX_TOKENS = 256  # reply allowance per question: room for providers that reason briefly anyway


_llm_prices: dict[str, tuple[float, float]] = {}


_reasoning: dict[str, dict] = {}  # per model: reasoning off where allowed (it hides logprobs), else minimal


def endpoint(model: str) -> dict:
    return ENDPOINTS[model.split(":", 1)[0]]


def llm_prices(model: str) -> tuple[float, float]:
    """(input, output) price per token: the endpoint's list price or, on OpenRouter, the dearest provider's for
    that model. OpenRouter's model listing shows the cheapest, and a pinned or fallback provider can charge 3x it,
    so the cap's worst case takes the most any of them charges. Spend is always the provider's reported cost."""
    if "price" in endpoint(model):
        return endpoint(model)["price"]
    mid = llm_route(model)[0]
    if mid not in _llm_prices:
        eps = (httpx.get(f"{OPENROUTER}/models/{mid}/endpoints", timeout=30).json().get("data") or {}).get("endpoints")
        if not eps:
            raise SystemExit(f"openrouter: no model {mid!r} with a live endpoint (see openrouter.ai/models)")
        _llm_prices[mid] = (max(float(e["pricing"]["prompt"]) for e in eps),
                            max(float(e["pricing"].get("completion") or 0) for e in eps))
    return _llm_prices[mid]


def price_per_token(model: str) -> float:
    """Input price; estimates ignore an LLM's few output tokens."""
    return llm_prices(model)[0] if is_llm(model) else PRICE_PER_INPUT_TOKEN


def llm_route(model: str) -> tuple[str, dict]:
    """"<endpoint>:<id>[@provider]" → (id, OpenRouter provider preferences). Pin a provider for reproducible
    answers: providers serve different builds of one model, and some ignore "reasoning off"."""
    mid, _, provider = model.split(":", 1)[1].partition("@")
    pref = {"require_parameters": True, **({"only": [provider]} if provider else {"sort": "price"})}
    return mid, pref


def estimate_cost(model: str, groups: list[list[dict]]) -> float:
    if model.startswith("distilled:"):
        return 0.0
    if engines.get(model):  # a plugin says only its worst case: estimate with that
        return sum(worst_cost(model, g) for g in groups)
    if not is_llm(model):
        return estimate_tokens(groups) * PRICE_PER_INPUT_TOKEN
    tokens = sum((len(json.dumps(it["state"])) + len(json.dumps(it["aq"]))) / 4 + LLM_OVERHEAD_TOKENS
                 for g in groups for it in g)  # one request per question
    return tokens * price_per_token(model)


def _bytes(x) -> int:
    return len(json.dumps(x, ensure_ascii=False).encode())


def worst_cost(model: str, g: list[dict]) -> float:
    """The most one request (one row's questions) can be charged, so --max-cost holds on the charged cost, not
    the estimate. Tokenizers spend at most one token per byte: every Jev request in the store (100,000+, 12 specs)
    was charged at most bytes + 184 tokens, under the 275-token overhead allowed here. An LLM may use its whole
    reply allowance."""
    if model.startswith("distilled:"):
        return 0.0
    if e := engines.get(model):
        return e.worst_cost(model, g[0]["state"], {it["rid"]: it["aq"] for it in g}) if hasattr(e, "worst_cost") else 0.0
    if not is_llm(model):
        return (_bytes(g[0]["state"]) + sum(_bytes(it["aq"]) for it in g) + REQUEST_OVERHEAD_TOKENS) * PRICE_PER_INPUT_TOKEN
    p_in, p_out = llm_prices(model)
    return sum((len(llm_prompt(it["aq"], g[0]["state"])[0].encode()) + LLM_OVERHEAD_TOKENS) * p_in
               + LLM_MAX_TOKENS * p_out for it in g)


def llm_prompt(aq: dict, state: dict) -> tuple[str, list[str], list[str]]:
    """(prompt, answer codes, labels). The fixed part (question, options) comes first so providers can cache it
    across rows; the row's input comes last. Options are numbered: one short token each, whatever the label."""
    t = aq["type"]
    if t == "choice":
        labels = list(aq["criteria"])
        opts = "\n".join(f"{i}. {k}: {v}" if v else f"{i}. {k}" for i, (k, v) in enumerate(aq["criteria"].items(), 1))
        codes, tail = [str(i) for i in range(1, len(labels) + 1)], "Answer with only the number of the best option."
    elif t == "noul":
        labels = codes = ["yes", "no"]
        crit = {str(k).lower(): v for k, v in (aq.get("criteria") or {}).items()}
        opts = "\n".join(f"{w.capitalize()} means: {crit[k]}" for w, k in (("yes", "true"), ("no", "false")) if crit.get(k))
        tail = "Answer with only yes or no."
    elif t == "score":
        labels = list(aq["criteria"])
        opts = "\n".join(f"{i}. {k}" for i, k in enumerate(labels))
        codes, tail = [str(i) for i in range(len(labels))], "Answer with only the number of the level that fits best."
    else:
        raise ValueError(f"engine openrouter: unsupported question type {t!r}")
    q = aq["instructions"] if isinstance(aq["instructions"], str) else json.dumps(aq["instructions"], ensure_ascii=False)
    prompt = (f"Question: {q}\n" + (f"\nOptions:\n{opts}\n" if opts else "")
              + f"\n{tail}\n\nInput (JSON):\n{json.dumps(state, ensure_ascii=False)}")
    return prompt, codes, labels


def llm_answer(aq: dict, codes: list[str], labels: list[str], logprobs: list[dict]) -> dict:
    """Probabilities over the options from the first token that is an answer code, renormalised over codes
    (top-20 logprobs: options outside the top 20 get 0). Same shapes as Jev's answers."""
    pos = next((x for x in logprobs if any(y["token"].strip().lower() in codes for y in x["top_logprobs"])), None)
    if pos is None:
        raise RuntimeError(f"no answer code among the output tokens {[x['token'] for x in logprobs[:5]]}")
    mass: dict[str, float] = {}
    for x in pos["top_logprobs"]:
        c = x["token"].strip().lower()
        if c in codes:
            mass[c] = mass.get(c, 0.0) + math.exp(x["logprob"])
    tot = sum(mass.values())
    probs = {lab: round(mass.get(c, 0.0) / tot, 4) for c, lab in zip(codes, labels)}
    if aq["type"] == "noul":
        return {"type": "noul", "noul": probs["yes"]}
    best = max(probs, key=probs.get)
    if aq["type"] == "choice":
        return {"type": "choice", "choice": best, "confidence": probs[best], "probabilities": probs}
    legend = {str(i): lab for i, lab in enumerate(labels)}
    return {"type": "score", "score": round(sum(i * probs[lab] for i, lab in enumerate(labels)), 4),
            "confidence": probs[best], "legend": legend,
            "probabilities": {str(i): probs[lab] for i, lab in enumerate(labels)}}


async def post(client: httpx.AsyncClient, sem, url: str, body: dict) -> dict:
    """POST with retries on rate limits, overload and transport errors. A request that was billed but whose reply
    was lost is billed again by its retry, and only the reply that arrives is counted: rare, and not in the cap."""
    last = "no response"
    async with sem:
        for attempt in range(8):
            try:
                r = await client.post(url, json=body)
            except httpx.TransportError as e:
                last = repr(e)
                settings.RETRIES["transport"] += 1
                await asyncio.sleep(0.5 * 2**attempt)
                continue
            if r.status_code in (429, 529) or r.status_code >= 500:
                last = f"HTTP {r.status_code}"
                settings.RETRIES[r.status_code] += 1
                await asyncio.sleep(min(float(r.headers.get("retry-after") or 0.5 * 2**attempt), 30))
                continue
            if r.status_code >= 400:
                raise RuntimeError(f"HTTP {r.status_code}: {r.text[:300]}")
            j = r.json()
            if "error" in j:  # OpenRouter reports some provider failures inside a 200
                last = str(j["error"])[:200]
                await asyncio.sleep(0.5 * 2**attempt)
                continue
            return j
    raise RuntimeError(f"gave up after retries ({last})")


async def ask(client: httpx.AsyncClient, sem, model: str, state: dict, questions: dict) -> dict:
    """{answers: {rid: answer}, tokens, cost, model} for one row's questions."""
    if e := engines.get(model):
        async with sem:
            got = await engines.call(e, model, state, questions)
        return {"answers": engines.check(model, questions, got), "tokens": int(got.get("tokens") or 0),
                "cost": float(got.get("cost") or 0.0), "model": model}
    if not is_llm(model):
        j = await post(client, sem, API, {"model": model, "state": state, "questions": questions})
        tokens = j["usage"]["input_tokens"]
        return {"answers": j["answers"], "tokens": tokens, "cost": tokens * PRICE_PER_INPUT_TOKEN, "model": j["model"]}

    async def one(rid: str, aq: dict) -> tuple[str, dict, dict]:
        prompt, codes, labels = llm_prompt(aq, state)
        mid, pref = llm_route(model)
        ep = endpoint(model)
        body = {"model": mid, "messages": [{"role": "user", "content": prompt}],
                "max_tokens": LLM_MAX_TOKENS,  # the answer's logprobs come after any brief reasoning
                # temperature 1 = the model's own distribution; at 0 some APIs return -9999 for every other token.
                # Only the probabilities are read, never the sampled text.
                "temperature": 1, "logprobs": True, "top_logprobs": 20, **ep.get("body", {})}
        if ep["base"] == OPENROUTER:
            body |= {"reasoning": _reasoning.get(model, {"effort": "none"}), "provider": pref}
        try:
            j = await post(client, sem, f"{ep['base']}/chat/completions", body)
        except RuntimeError as e:
            minimal = {"effort": "minimal"}  # measured on GLM: 0 reasoning tokens, logprobs kept
            if "mandatory" not in str(e) or body["reasoning"] == minimal:
                raise
            _reasoning[model] = body["reasoning"] = minimal
            j = await post(client, sem, f"{ep['base']}/chat/completions", body)
        c = j["choices"][0]
        lp = (c.get("logprobs") or {}).get("content") or []
        try:
            u = j["usage"]
            if "cost" not in u:
                pin, pout = ep["price"]
                u["cost"] = u["prompt_tokens"] * pin + u.get("completion_tokens", 0) * pout
            return rid, llm_answer(aq, codes, labels, lp), u
        except RuntimeError as e:
            raise RuntimeError(f"{e}; provider {j.get('provider')}, reply {c['message'].get('content')!r}, "
                               f"finish {c.get('finish_reason')} (this provider may reason despite 'off'; pin one with "
                               f"openrouter:<id>@<provider>)") from None

    got = await asyncio.gather(*[one(rid, aq) for rid, aq in questions.items()])
    return {"answers": {rid: a for rid, a, _ in got}, "tokens": sum(u["prompt_tokens"] for *_, u in got),
            "cost": sum(u.get("cost", 0.0) for *_, u in got), "model": model}


async def fill(spec: dict, db, items: list[dict]) -> tuple[dict, dict]:
    """Ensure every item has an answer. Missing questions of the same row share one request (read once).
    Each response is saved as it arrives, so a failure part-way loses nothing already paid for."""
    have = cached(db, list({it["key"] for it in items}))
    missing: dict[str, dict[str, dict]] = {}
    for it in items:
        if it["key"] not in have:
            # one request per distinct state (not per row id: two rows can share an id and differ in text)
            missing.setdefault(it["shash"], {})[it["key"]] = it  # dedupe identical keys within a request
    group = [list(g.values()) for g in missing.values()]
    model = spec["model"]
    stats = {"cached": sum(it["key"] in have for it in items), "asked": 0, "tokens": 0, "cost": 0.0,
             "requests": sum(len(g) for g in group) if is_llm(model) else len(group)}

    if group and model.startswith("distilled:"):  # a local student (hunch distill): no key, no cost, no network
        from hunch import distill
        got = distill.answer(spec, model, [it for g in group for it in g])
        write(db, "insert or replace into answers (key, model, answer, input_tokens) values (?, ?, ?, 0)",
              [[k, model, json.dumps(a)] for k, a in got.items()])
        have.update(got)
        stats["asked"] += len(got)
        return have, stats
    if group:
        for w in oversized([it for g in group for it in g])[:5]:
            print(f"  size warning: {w}", file=sys.stderr)
        est = estimate_cost(model, group)
        cap = settings.MAX_COST  # this fill's cap: --max-cost, and what is left of a total across fills
        if settings.SPEND_LIMIT is not None:
            cap = max(0.0, settings.SPEND_LIMIT - settings.CHARGED) if cap is None else min(cap, max(0.0, settings.SPEND_LIMIT - settings.CHARGED))
        if cap is not None and est > cap:
            raise SystemExit(f"{spec.get('judgment', '')}: would ask {sum(map(len, group))} answers in {len(group)} requests "
                             f"(~${est:.4f}), above --max-cost ${cap:.4g}; nothing asked")
        print(f"  asking {sum(map(len, group))} answers in {stats['requests']} requests (~${est:.4f})", file=sys.stderr)
        plugin = engines.get(model)
        var = getattr(plugin, "key", None) if plugin else endpoint(model)["key"] if is_llm(model) else "TYPESAFE_API_KEY"
        key = (os.environ.get(var) if var else "none needed") or (None if is_llm(model) or plugin else os.environ.get("TYPESAFE_AI_API_KEY"))
        if not key:
            raise SystemExit(f"{spec.get('judgment', '')}: set {var} to ask {model} ({len(group)} requests to send)")
        # The estimate can be low (chars/4 undercounts dense text: 20% on BANKING77, 41% on shell commands), so the
        # cap is also kept while asking: a request is sent only if the charged cost so far, plus the worst case of
        # every request in flight and of this one, stays within it. Asyncio runs one task at a time, so the check
        # and the reservation can't interleave.
        held, stop, over = [0.0], [0], []
        async with httpx.AsyncClient(headers={"Authorization": f"Bearer {key}"}, timeout=120) as client:
            n = getattr(plugin, "concurrency", None) or CONCURRENCY
            sem, gate = asyncio.Semaphore(n), asyncio.Semaphore(n)

            async def one(g: list[dict]) -> None:
                async with gate:
                    worst = worst_cost(model, g)
                    if cap is not None and (stop[0] or stats["cost"] + held[0] + worst > cap):
                        stop[0] += 1
                        return
                    held[0] += worst
                    try:
                        res = await ask(client, sem, model, g[0]["state"], {it["rid"]: it["aq"] for it in g})
                    finally:
                        held[0] -= worst
                    if plugin and cap is not None and res["cost"] > worst + 1e-12:  # its worst case was wrong
                        over.append((res["cost"], worst))
                        stop[0] += 1
                tokens = res["tokens"]
                write(db, "insert or replace into answers (key, model, answer, input_tokens) values (?, ?, ?, ?)",
                      [[it["key"], res["model"], json.dumps(res["answers"][it["rid"]]), tokens / len(g)] for it in g])
                stats["tokens"] += tokens
                stats["cost"] += res["cost"]
                settings.CHARGED += res["cost"]
                stats["asked"] += len(g)
                for it in g:
                    have[it["key"]] = res["answers"][it["rid"]]

            t0, r0 = time.perf_counter(), dict(settings.RETRIES)
            results = await asyncio.gather(*[one(g) for g in group], return_exceptions=True)
            dt = time.perf_counter() - t0
            retried = {k: v - r0.get(k, 0) for k, v in settings.RETRIES.items() if v - r0.get(k, 0)}
            if settings.VERBOSE or retried:
                print(f"  {stats['requests']} requests in {dt:.1f}s ({stats['requests'] / dt:.1f}/s at concurrency {n})"
                      + (f"; retried {retried}" if retried else ""), file=sys.stderr)
        failed = [r for r in results if isinstance(r, BaseException)]
        broke = next((r for r in failed if isinstance(r, engines.ContractError)), None)
        if broke:  # a plugin's bug: retrying won't fix it
            raise SystemExit(f"{spec.get('judgment', '')}: {broke} ({stats['asked']} answers saved)")
        if over and cap is not None:
            raise SystemExit(f"{spec.get('judgment', '')}: engine {model.split(':', 1)[0]!r} charged ${over[0][0]:.6f} for "
                             f"one request, above the ${over[0][1]:.6f} its worst_cost allows, so --max-cost can't "
                             f"hold; stopped after ${stats['cost']:.4f} ({stats['asked']} answers saved)")
        if failed:
            raise RuntimeError(f"{len(failed)}/{len(group)} requests failed; {stats['asked']} answers saved, "
                               f"re-run to retry only the rest. First error: {failed[0]}") from failed[0]
        if stop[0]:
            raise SystemExit(f"{spec.get('judgment', '')}: stopped at --max-cost ${cap:.4g}: ${stats['cost']:.4f} charged for "
                             f"{stats['asked']} answers (saved); {stop[0]} requests not sent, as the next could have "
                             f"gone over. Re-run with a higher --max-cost to ask only the rest")
    return have, stats


def merge_stats(*stats: dict) -> dict:
    return {k: sum(s[k] for s in stats) for k in ("cached", "asked", "requests", "tokens", "cost")}


STATE_LIMIT_TOKENS = 32_000  # Jev: state + the longest question must fit in 32k tokens (64k per request)


WARN_AT = 0.8


def oversized(items: list[dict]) -> list[str]:
    """Rows near the engine's input limit, with their largest column: past it the request fails, and clipping
    (spec `clip:`) is the fix."""
    out, seen = [], set()
    for it in items:
        if it["id"] in seen:
            continue
        seen.add(it["id"])
        tokens = (len(json.dumps(it["state"])) + len(json.dumps(it["aq"]))) / 4
        if tokens > WARN_AT * STATE_LIMIT_TOKENS:
            parts = state_parts(it["spec"], it["state"])
            big = max(parts, key=lambda c: len(str(parts[c])))
            out.append(f"row {it['id']}: ~{tokens:,.0f} tokens (limit {STATE_LIMIT_TOKENS:,}); largest column {big!r} "
                       f"~{len(str(parts[big])) / 4:,.0f} → clip: {{{big}: N}}")
    return out


def estimate_tokens(groups: list[list[dict]]) -> float:
    """Input tokens for these requests (one per group of items sharing a row): ~chars/4 plus a measured fixed
    overhead per request. An estimate: 3% high on the command guard recipe, 20% low on BANKING77 (77 long
    options), 41% low on real shell commands; --max-cost is kept on the charged cost (worst_cost)."""
    return (sum(len(json.dumps(g[0]["state"])) + sum(len(json.dumps(it["aq"])) for it in g) for g in groups) / 4
            + REQUEST_OVERHEAD_TOKENS * len(groups))
