# /// script
# requires-python = ">=3.12"
# dependencies = ["httpx", "pyyaml"]
# ///
"""Blind packets for the rows where DeepSeek disagrees with the BANKING77 answer key and no one has reviewed
the row yet (Jev agreed with the key there). Same protocol as the tree round: intents defined by 3 train
examples each, two candidates in random order, sources hidden."""
import json
import random
import sys
from pathlib import Path

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE.parents[2] / "src"))
from hunch.answers import (  # noqa: E402  (engine internals: items, store, reviews)
    attach_gold,
    decide,
    hit,
    load_reviews,
)
from hunch.spec import load_spec, plan  # noqa: E402  (engine internals: items, store, reviews)
from hunch.store import cached, open_store  # noqa: E402  (engine internals: items, store, reviews)

EX = HERE.parent.parent / "examples" / "banking77"
spec = load_spec(EX / "intent.yml")
spec["source"] = (EX / "banking77_holdout.csv").resolve()
spec["model"] = "deepseek:deepseek-flash"
its = plan(spec)
answers = cached(open_store(spec), [it["key"] for it in its])
attach_gold(its, load_reviews(spec))
todo = [it for it in its if it["gold_src"] == "source" and not hit(it, answers[it["key"]], "raw_gold")]
intents = json.load(open(HERE.parent / "tree/packets/b77_opus.json"))["intents"]
rnd = random.Random(2026)
rows, key = [], {}
for n, it in enumerate(sorted(todo, key=lambda it: int(it["id"])), 1):
    gold, model = next(iter(it["raw_gold"])), decide(answers[it["key"]])[0]
    cands = [gold, model]
    rnd.shuffle(cands)
    rows.append({"n": n, "text": it["row"]["text"], "candidates": cands})
    for rev in ["opus", "sonnet_a", "sonnet_b"]:
        key[f"{rev}:{n}"] = {"id": it["id"], "gold": gold, "model": model, "kind": "disagree", "state_hash": it["shash"]}
for rev, order in [("opus", rows), ("sonnet_a", rows), ("sonnet_b", rows[::-1])]:
    json.dump({"intents": intents, "rows": order}, open(HERE / f"packets/b77_{rev}.json", "w"), indent=1, ensure_ascii=False)
json.dump(key, open(HERE / "key/b77_map.json", "w"), indent=1)
print(len(rows), "rows")
