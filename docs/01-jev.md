# 01 — Jev and System One models

Source: field note "Pointing, Not Writing" (https://claude.ai/artifact/L1vfJCASSY7b7FFTunksnu, Sep 2026) plus the sources it cites (listed at the end). Figures are as reported there, not independently re-verified.

## What it is

Jev is TypeSafe's "System One" model (named after Kahneman's System 1: fast, intuitive). It **never writes free text**. You send:

- **state**: the text/data to look at
- **questions**, each with answers you define

It returns calibrated probabilities over your options. Three primitives:

| Primitive | Question | Returns |
|---|---|---|
| `choice` | "Which team should handle this?" | distribution over YOUR options (billing 0.15, technical 0.85, sales 0.00) |
| `score` | "How frustrated is the customer?" (0 calm … 2 very angry) | position on YOUR scale |
| `noul` | "The message conveys urgency." | probability it's true |

Because you wrote the options, it cannot return something off-menu: no invented labels, no broken JSON, no parsing. It can pick the *wrong* option, never a nonexistent one.

Named after William Stanley Jevons (Jevons paradox, 1865): cheaper per-unit cost → far more total use. Cheap judgment → judgment everywhere.

## The three ideas that matter

### 1. Turn generation into selection
Don't ask a model to write the date; regex all date-shaped candidates, ask Jev which is the delivery date, copy exact characters. Nothing is ever made up.

### 2. Read once, ask many
The state is read once; every question branches off that single reading, in parallel, isolated. Implications:
- 13 questions over one article: 12.2× cheaper, 10.0× faster than 13 calls, identical answers (TypeSafe).
- Server time stayed flat up to ~100 questions (independent probe).
- **Speculative fan-out**: ask questions you might not need ("if click, which element? if type, which box?"); code reads the branch that applies.

### 3. Calibrated probabilities
Trained so "80% sure" is right ~80% of the time. This gives software a knob: act above threshold, escalate below. Examples:
- PR-review bot sends only the 0.35–0.65 band to a human.
- Tax-form classifier: <95% → human; 753 pages, 0 wrong, 5% reviewed.
- Measured calibration error: 0.065 (Decision Index); one sentiment test 0.151 (over-confident) until corrected. Good, not perfect.

## Economics

- Price: **$0.042 per 1M input tokens; output free.**
- Homepage demo: Jev $0.000081 / 0.114 s vs chat LLM $0.013880 / 8.566 s (~170× cheaper). Independent: ~250 ms median over network.

| Workload | Cost |
|---|---|
| One support-ticket decision (Jev) | $0.000081 |
| Same, chat LLM | $0.013880 |
| Filter 129 DB rows (Postgres `jev()`) | $0.0009 (6 ms cached repeat) |
| Flight search in browser (Jev + small LLM) | $0.0039 |
| Review 1,000 PRs (Jev, 14 checks each) | ~$0.07 |
| Review 1,000 PRs (frontier LLM) | ~$14.50 |
| 8 questions × 3,282 posts (26,256 judgments) | $0.1282 |

## Pattern: propose → decide → act

Code or an LLM proposes options → Jev picks/scores → code acts → the unsure band escalates to a bigger LLM or a human. Browser agent: 1,092 → 101 browser commands on Google Flights, 7 s, <$0.005. WebMCP 49-task benchmark: Jev (tool choice) + small LLM (arguments) solved 49/49 at ~$0.001 each, first of 21 setups.

## Applications seen in the wild (first weeks)

- **fast-jev-compaction**: for every tool call in a Claude Code session ask "still needed? result needed verbatim?" → ~1M tokens to 86k in ~1 s. Lesson: small questions about every piece beat summarizing the whole.
- **Postgres `WHERE jev(people, 'could work from home')`**; DuckDB version chaining dependent yes/no questions. Lesson: cheap judgment becomes an ordinary operator.
- **Spreadsheet column that reads its header**, app launcher ranking by meaning per keystroke (~100 ms). Lesson: at 100 ms AI becomes the interface.
- **Agent supervisors**: "is the goal actually complete?", drift fuse, "is this retry genuinely different?". Lesson: fast model babysits slow one.
- **14 PR-review checks in one call** ($0.00007). Lesson: a probability per risk beats a paragraph.
- **Halite hybrid**: LLM plans, Jev moves ships; 13× faster, 56% cost, slightly better. But LLM alone beat Jev alone 82% of the time. Jev is the soldiers, not the general.

## Limits ("jagged edges") — these drive hunch's test design

| Limit | Evidence | Mitigation |
|---|---|---|
| Reads literally | answers the question written, not meant | put the "what I really meant" into the question |
| Can't count / do math | — | arithmetic, dates, comparisons in code |
| **Option order matters** | reversing options moved one prob 0.43 → 0.63 | ask several orderings, average → **order-stability test** |
| **Questions don't add up** | "refund?" 0.72 + "not a refund?" 0.47 = 1.19 | don't rely on cross-question logic → **consistency test** |
| Clutter hurts | irrelevant state lowers accuracy | filter first, then ask |
| No multi-step reasoning | crawl start choice 27% vs 93%; single-page tagging 85% vs 56% (win) | keep reasoning in LLM/code |

Fair criticism: label-probability reading is an old trick; GLiNER2 (2025) did schema-defined classification. New: frontier-level understanding + request-time labels + calibration + speed/price. ~31 open copies within a week; independent 37-benchmark Decision Index: Jev 59.5, best open ~4 points behind → **engine must be pluggable**.

## Sources

Official: [TypeSafe docs](https://docs.typesafe.ai/introduction.md), [models & pricing](https://docs.typesafe.ai/models.md), [Jev 1.13 jaggedness](https://docs.typesafe.ai/model-jaggedness/jev-1.13.md), [parallel questions cookbook](https://docs.typesafe.ai/cookbooks/parallel_questions.md), [launch post](https://typesafe.ai/blog/introducing-system-one-models-and-jev).
Independent: [Decision Index 0.1](https://huggingface.co/spaces/multimodalart/jev-decision-index), [Archer Hume probes](https://archerhume.com/posts/jevs-architecture-unmasked/?v=3), [Anthus calibration test](https://anth.us/blog/jev-vs-laya/), [WebMCP benchmark](https://webmcp.com/benchmark), [Browser Use Ultrafast](https://github.com/browser-use/jev-ultrafast), [fast-jev-compaction](https://github.com/tamaratran/fast-jev-compaction), [tax-doc-classifier](https://github.com/kyotofin/tax-doc-classifier), [pijev](https://github.com/TypeLLM/pijev).
