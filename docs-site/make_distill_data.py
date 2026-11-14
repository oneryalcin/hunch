"""Rebuild snippets/distill-data.jsx for the distill guide, from the BANKING77 example's stored answers.

    uv run --extra distill python docs-site/make_distill_data.py     # from the repo root; asks nothing (HUNCH_MAX_COST=0)

STUDENT_ROWS: the example's student (the model intent_distilled.yml names) on the 385 holdout messages, alone, in
the shape the act dial reads. The counts it prints are the dial `hunch test --model distilled:…` prints.
MESSAGE_MAP: training messages of four intents, each placed by its embedding from the student's encoder, flattened
to the two directions that keep the most spread (a principal component analysis). The intent is the one Jev gave.
It also prints the figures the guide quotes and images/distill-student.svg draws, so they can be checked again.
"""
import json
import os
import re
from pathlib import Path

os.environ["HUNCH_MAX_COST"] = "0"
import numpy as np  # noqa: E402

from hunch.answers import attach_gold, conf_of, decide, hit, load_reviews  # noqa: E402
from hunch.distill import answer, encode  # noqa: E402
from hunch.execute import execute  # noqa: E402
from hunch.spec import DIAL, item, load_project  # noqa: E402

EXAMPLE = Path("prototype/examples/banking77")
MODEL = load_project(EXAMPLE / "intent_distilled.yml")["nodes"]["intent_distilled"]["model"]
# two intents that mean different things, and two that mean nearly the same
INTENTS = ["exchange_rate", "top_up_failed", "card_arrival", "card_delivery_estimate"]


def answers(source: str, model: str | None = None) -> dict:
    project = load_project(EXAMPLE / "intent.yml")
    spec = project["nodes"]["intent"]
    spec["source"] = (EXAMPLE / source).resolve()
    if model:  # as `--model` does: the student's results get their own table
        spec["model"], spec["_table_suffix"] = model, "__" + re.sub(r"\W+", "_", model).strip("_")
    res = execute(project)["intent"]
    attach_gold(res["items"], load_reviews(spec))
    return res


res = answers("banking77_holdout.csv", MODEL)
jev = answers("banking77_holdout.csv")
by_jev = {it["id"]: hit(it, jev["answers"][it["key"]], "gold") for it in jev["items"]}
rows, pair = [], 0
for it in res["items"]:
    a = res["answers"][it["key"]]
    ok = hit(it, a, "gold")
    pair += ok if conf_of(it, a) >= 0.9 else by_jev[it["id"]]  # below act the teacher's answer replaces the student's
    # [confidence, right?, the student's answer, gold if wrong, start of the message]
    rows.append([round(conf_of(it, a), 4), int(ok), decide(a)[0], "" if ok else " or ".join(sorted(it["gold"])), it["state"]["text"][:70]])
rows.sort(key=lambda r: r[0])
print(f"{MODEL}: {len(rows)} holdout rows, right on {sum(r[1] for r in rows) / len(rows):.1%}; "
      f"with Jev behind it at 0.90 {pair / len(rows):.1%}; Jev alone {sum(by_jev.values()) / len(by_jev):.1%}")
for t in DIAL:
    auto = [r for r in rows if r[0] >= t]
    print(f"  {t:.2f}  answered locally {len(auto) / len(rows):.1%}  wrong {sum(not r[1] for r in auto)} of {len(auto)}")

res = answers("banking77_unlabeled.csv")
criteria = res["spec"]["questions"]["intent"]["criteria"]
picked = [(it["state"]["text"], INTENTS.index(label)) for it in res["items"] if it["key"] in res["answers"]
          for label in [decide(res["answers"][it["key"]])[0]] if label in INTENTS]
encoder = json.loads((EXAMPLE / MODEL.split(":", 1)[1] / "meta.json").read_text())["encoder"]
X = np.asarray(encode(encoder, [t for t, _ in picked]), dtype=np.float64)
group = np.array([g for _, g in picked])
centred = X - X.mean(0)
_, spread, axes = np.linalg.svd(centred, full_matrices=False)
axes = axes[:2] * np.sign(axes[:2, np.abs(axes[:2]).argmax(1)].diagonal())[:, None]  # a fixed sign, whatever the library
flat = centred @ axes.T
flat[:, 1] *= -1  # screen y grows downwards
box = (flat - flat.min(0)) / (flat.max(0) - flat.min(0)).max()  # one scale for both axes: distances keep their ratio
points = sorted([int(round(x * 1000)), int(round(y * 1000)), int(g), t[:70]] for (x, y), g, (t, _) in zip(box, group, picked))


def by_nearest_centre(Z) -> float:
    centres = np.stack([Z[group == g].mean(0) for g in range(len(INTENTS))])
    return float((np.argmin(((Z[:, None] - centres[None]) ** 2).sum(-1), 1) == group).mean())


print(f"map: {len(points)} messages with {encoder} ({X.shape[1]} numbers each), "
      + ", ".join(f"{name} {int((group == g).sum())}" for g, name in enumerate(INTENTS)))
print(f"  two directions keep {(spread[:2] ** 2).sum() / (spread ** 2).sum():.0%} of the spread; "
      f"the nearest group centre is Jev's intent for {by_nearest_centre(flat):.1%} of messages on the map, "
      f"{by_nearest_centre(X):.1%} with all {X.shape[1]} numbers")

# quoted in the guide: how close two messages are, what the student says of two messages, and the diagram's numbers
QUOTED = ["Where is my card?", "My card hasn't arrived yet", "How do I change my PIN?", "When will my card get here?"]
E = np.asarray(encode(encoder, QUOTED), dtype=np.float64)
print(f"closeness: {QUOTED[0]!r} and {QUOTED[1]!r} {E[0] @ E[1]:.2f}; {QUOTED[0]!r} and {QUOTED[2]!r} {E[0] @ E[2]:.2f}")
student = load_project(EXAMPLE / "intent_distilled.yml")["nodes"]["intent_distilled"]
for text in (QUOTED[1], QUOTED[3]):
    it = item(student, {"id": "quoted", "text": text}, "intent")
    p = sorted(answer(student, MODEL, [it])[it["key"]]["probabilities"].items(), key=lambda kv: -kv[1])
    print(f"student on {text!r}: {p[0][0]} {p[0][1]}, {p[1][0]} {p[1][1]}, the other {len(p) - 2} together {1 - p[0][1] - p[1][1]:.4f}")
print(f"first 16 numbers of {QUOTED[1]!r}: {[round(float(v), 3) for v in E[1][:16]]}")

out = Path(__file__).parent / "snippets" / "distill-data.jsx"
dump = lambda v: json.dumps(v, ensure_ascii=False, separators=(",", ":"))  # noqa: E731
out.write_text("// Generated by docs-site/make_distill_data.py from prototype/examples/banking77 (BANKING77, PolyAI, CC BY 4.0). Do not edit.\n"
               f"export const STUDENT_ROWS = {dump(rows)};\n"
               f"export const MESSAGE_MAP = {dump({'groups': [[g, criteria[g]] for g in INTENTS], 'points': points})};\n")
print(f"-> {out} ({out.stat().st_size // 1024} KB)")
