# 04 — Design

Status: sketch. Nothing here is built or validated yet.

## Decision: clean slate, not on dbt

Judgment workloads differ from SQL transforms at the core. Bending dbt would mean fighting it.

| Judgments need | dbt assumes |
|---|---|
| Network call per row: batching, rate limits, retries, partial failure, cost budgets | One SQL statement the warehouse runs |
| Per-row cache and provenance (which question version produced this label) | Model = a table; no row state |
| Human in the loop: review queue with write-back | No concept |
| Statistical tests (calibration, order stability) on gold sets | Tests = failing rows |
| Backtest across question versions | No history of past outputs |
| Sources beyond the warehouse: traces, files, APIs, streams | Warehouse tables |
| **Same definition online (per request in the app) and batch** | Batch only |

The last row alone justifies a new tool: define a judgment once, run it inside an app request **and** as a backfill.

**Cost of going standalone:** lose dbt's distribution; rebuild DAG, selectors, envs, docs. **Mitigation: interop, not dependency** (below).

## Core primitive: content-addressed judgments

Every result keyed by:

```
key = hash(rendered_state + question + options + engine + engine_version)
```

Everything falls out of this, like everything falls out of `ref()`:

| Feature | How |
|---|---|
| Incremental runs | Only rows whose key changed cost money |
| Backtest diff | Two question versions → two key sets → join → exactly which rows flipped |
| Reproducibility / audit | Any label traces to exact inputs |
| Online serving | Repeated inputs served from cache (cf. Postgres `jev()` 6 ms repeat) |
| Review write-back | Human answer stored against the same key → gold data |

Open: does the key include the rendered state text, or a hash of source row + template? (Rendered text is simpler and more honest; template changes should invalidate.)

## Concepts

| Concept | Meaning |
|---|---|
| **source** | Where rows come from: CSV/Parquet, DuckDB/warehouse query, dbt model (via manifest), trace store (OTel), API |
| **judgment** | state template over a source/ref + one or more questions. Materializes to a table |
| **question** | `choice` (options), `score` (scale with anchors), `noul` (statement) |
| **threshold / route** | Per question: act ≥ t_high, escalate between, maybe reject ≤ t_low |
| **review queue** | Materialization of escalated rows; answers write back by key |
| **gold set** | Seeds of labelled rows (hand-made or from reviews) |
| **test** | Assertions over a judgment (see below) |
| **engine** | Pluggable backend: Jev first; LLM logprob fallback |
| **store** | Content-addressed result store (DuckDB/Parquet locally) |

## Spec sketch (illustrative, not final)

```yaml
# judgments/ticket_triage.yml
judgment: ticket_triage
source: ref('support_tickets')           # a source, another judgment, or a dbt model
state: |
  Subject: {{ subject }}
  Body: {{ body }}
questions:
  department:
    choice: [billing, technical, sales]
    route: { act: 0.80, escalate: 0.35 }
  frustration:
    score:
      0: calm
      1: frustrated but civil
      2: very angry
  urgent:
    noul: "The message conveys urgency."
tests:
  - gold: seeds/ticket_triage_gold.csv
    accuracy: { min: 0.90 }
    calibration_error: { max: 0.08 }
  - order_stability: { question: department, permutations: 3, max_flip_rate: 0.02 }
  - consistency: { pair: [urgent, not_urgent], sum_within: 0.15 }
```

Online, same spec:

```python
from hunch import judge
r = judge("ticket_triage", subject=s, body=b)   # cache → engine; same key space as batch
if r.department.route == "act": ...
```

## Tests (derived from engine failure modes, see 01-jev limits)

| Test | Catches |
|---|---|
| gold accuracy | wrong answers vs labelled rows |
| calibration error (ECE) | confidence that can't be trusted for routing |
| order stability | option-order sensitivity (0.43 → 0.63) |
| consistency | complementary questions not summing ≈1 (0.72 + 0.47) |
| drift | distribution shift in labels/confidence between runs |
| classic row tests | `not_null`, `accepted_values` on outputs |

## CLI sketch

- `hunch compile` — render exact engine payloads (legibility, like dbt compiled SQL); cost estimate.
- `hunch run [-s selector]` — dbt-style selectors (`+model`).
- `hunch test`
- `hunch diff --against main` — backtest: rows that flip + samples. The CI hero feature.
- `hunch review` — local review of the queue (TUI or minimal web).

## Architecture

- **The spec is the product.** Language-neutral YAML; SDKs thin (Python first, TS later). A spec that becomes a standard is the durable asset.
- **Core:** compiler + graph + executor (async, batching, retries, budget) + content-addressed store.
- **Engines pluggable.** Jev first. Single proprietary vendor = adoption risk (pricing, uptime, enterprise questions); ~31 open copies exist.
- **Language: Python.** Bottleneck is network I/O, not CPU; Rust buys nothing (Fusion needed Rust for SQL parsing, we don't). Data people live in Python.
- **Local-first:** DuckDB/Parquet store; warehouse write-back adapters later.

## dbt interop (not dependency)

- Read `manifest.json` → dbt models usable as sources.
- Write outputs back as warehouse tables.
- Emit dbt `sources:` YAML so downstream dbt models can `ref()` judgments.
- Borrow dbt selector syntax and conventions to cut learning curve.

## Scope discipline (non-goals)

No extraction/loading, no general orchestration, no general LLM app framework, no free-text generation. Only: judgments over rows, their tests, their diffs, their review loop.

## Open questions

- Persona: analytics engineer, AI/app engineer, or ops? Online + batch spans two audiences — pick one to lead.
- Multi-question calls: batch all questions of a judgment into one engine call per row (read-once) — yes by default; how to expose speculative fan-out?
- Score questions: how does calibration apply to ordinal scales?
- Store format and GC for old versions.
- Trace ingestion format for evals (OTel GenAI semantic conventions?).
- PyPI name (see README).
- Judgment → rule demotion (from Roast's "AI as placeholder" idea, see 03 related work): if a deterministic rule matches the judgment on ≥X% of gold rows, suggest replacing it (`hunch suggest-rules`)?

## First prototype (throwaway)

1. One YAML judgment over a CSV loaded in DuckDB.
2. Content-addressed cache in DuckDB.
3. Change question wording → `diff` prints flipped rows with before/after probabilities.

Success criterion: the diff feels magical.

**Built 2026-09-24 → `prototype/`** (results table in `prototype/README.md`). Verdict: it does. A wording change surfaced "Custom contract now counts as urgent" before shipping, for $0.0005.

## Findings from the prototype (measured on jev-1.13.0)

- **Jev is not deterministic.** Identical requests: ambiguous choice p ranged 0.70–0.80 (sd 0.029, n=12); noul sd ≈0.005; clear cases sd 0. Consequences:
  - Reproducibility must come from the **cache**, not the model. Content addressing is not an optimization, it's the only way labels stay stable across runs.
  - `diff` must separate real flips from noise: prototype flags flips within 0.10 of the boundary as `~noise`. Better later: estimate per-row noise by resampling borderline rows.
  - Tests near thresholds are flaky if they re-ask; always evaluate on cached answers.
- **Per-question cache keys are valid.** Asking a question alone vs with others, or under a different id, stayed within the noise band (0.71 / 0.73 / 0.74 / 0.78). So keys are per (row, question), and requests still batch all missing questions of a row (read once).
- **Pin exact model versions.** `jev-1.13.0` accepted; `jev-1.13` rejected. `jev-latest` in a key would silently mix model versions → spec should require an exact version (or resolve and record).
- **Fixed overhead ≈275 input tokens per request** (beyond ~chars/4). Estimate = chars/4 + 275 × requests; landed within 5.5% of actual.
- **Backtest semantics: old logic on today's data.** The old spec must resolve sources against the current spec's location, else the diff compares different inputs. (First bug hit.)
- **Routing and test config must stay out of the key.** Changing `act` re-routes rows at $0, no calls.
- **Option descriptions matter a lot.** Bare labels → 97.5%, 2 rows below 0.80; with one-line descriptions → 100%, 1 row below 0.95.
- Latency: 40 requests in ~1.8 s wall with concurrency 16.

## Findings, round 2: real data, tests, online (BANKING77, 2026-09-24)

Full numbers in `prototype/README.md`. Dev 770 rows, disjoint holdout 385, 77 intents.

- **Cache key must preserve option order.** First prototype used `sort_keys=True`: reordering options (which changes answers) hit the old cache entry. Any canonicalization must be semantics-preserving, and order is semantics here.
- **Describe all options or none.** Partial descriptions (29/77) attracted rows from undescribed neighbours into described ones; holdout gain was not significant (22 fixed / 14 broken, p=0.24). Describing all 77 from the train split: 82.3% → 88.3% on holdout, 28/5, p<0.001. → Lint rule: warn when a choice has a mix of described and bare options.
- **Label names lie.** BANKING77's `get_physical_card` is about PINs. Bare labels can't work when names mislead; descriptions are not optional polish.
- **Significance is a feature.** Dev said v2 clearly won (p=0.005) because dev confusions chose what to describe; holdout disagreed. `diff` must print a paired sign test, and hunch should push a dev/holdout split (tune on one, report on the other). The tickets "97.5% → 100%" was one row, p=1.0.
- **Confident mistakes are mostly gold errors.** Of 23 high-confidence misses adjudicated: 13 gold wrong, 10 ambiguous, 0 clearly model wrong. → Review queue needs a "gold disputed" path: human verdict corrects the gold set, not just the label. Measured calibration error is inflated by gold noise; calibration needs clean gold to mean anything.
- **Jev overconfident but usable.** Holdout calibration error 0.053–0.066 (the independent Decision Index reported 0.065). Top bin states ~0.99, observes ~0.96. The dial table is what operators actually need: v3 automates 81% at 4.2% error at 0.90, 65% at 2.0% at 0.99.
- **Order sensitivity is real but confined to the uncertain band.** 3.7–8.3% flips on a sample; all v1 flips had p ≤ 0.74. Better descriptions halved it (6.7% → 3.7%, mean |Δp| 0.055 → 0.032). Order test only matters for rows below `act`.
- **Online judge works and shares keys both ways** (batch → online hit 9–20 ms; online → next batch $0).
- **Store must be multi-process.** DuckDB single-writer lock: `judge()` fails whenever a batch runs. SQLite WAL: 0 errors in ~47k mixed concurrent ops, but tail latency up to 2.8 s under a bulk writer, and WAL must be enabled once at creation. → Online path: read-only lookups on the hot path, writes queued off the request path. Server edition: Postgres. DuckDB stays useful for analytics over materialized results, not as the cache.
- Costs: 77-option request ≈ 2k input tokens ($0.00009/row); whole round $0.30.

## Findings, round 3: store, review, lint, agent-trace evals (2026-09-24)

- **Store: SQLite WAL works for batch + online on one machine.** Online `judge()` during a real batch writing 200 answers: 63 hits, p50 0.6 ms, p99 3.0 ms, 0 errors (DuckDB failed outright). Direct short write transactions were enough; the 2.8 s stalls in the earlier synthetic test came from tight-loop 200-row transactions, not realistic load. WAL must be switched on with retries (it needs a moment alone with the file). Server edition still wants Postgres.
- **Save each response as it arrives.** The first prototype saved only after every request finished: one failure lost everything already paid for. Now a failed run reports how many answers were saved and a re-run asks only for the rest.
- **Normalize line endings in the state, both sent and hashed.** A stress test "found" a cache bug that was really `\r\n` vs `\n` (174 of 200 traces differ). Apps posting forms and batches reading files would silently miss and possibly get different answers. Scores barely moved after normalizing (AUROC 0.834 → 0.835), so the ending carries no meaning here. Normalizing only the hash would be wrong: two different inputs would share a key.
- **Reviews belong in git, not in the cache.** Verdicts go to `<judgment>.reviews.csv` next to the spec, keyed by row id + hash of the row's text (a verdict on text that changed is ignored). Review queue = *disputed* (confident answer ≠ gold) + *uncertain* (below act, no gold).
- **Gold correction moves every metric.** 13 reviewed rows (3% of holdout): accuracy 88.3% → 91.0%, calibration error 0.053 → 0.030, auto-acted accuracy 95.8% → 99.3%. **But reviewing only disputes is biased toward the model** (wrong gold that agrees with the model is never seen). → The queue needs a random audit slice to estimate gold error everywhere; report reviewed-gold numbers as an upper bound until then.
- **Lint must not block on missing gold.** Production rows have no gold; a missing gold column is a warning (still catches typos), not an error. Found by the online demo.
- **Agent-trace evals work, as triage.** Resolved-or-not from issue + patch + final messages: AUROC 0.835 without running tests. Confident "no" is reliable (p < 0.2 → 51/53 failed); confident "yes" is rare. → Noul routing needs **asymmetric thresholds** (`act_yes`, `act_no`), not one `act` on max(p, 1−p).
- **Overclaiming is measurable.** Agents claimed a fix in 153/200 runs and 39% of those failed; a cheap per-turn check flags a third of the false claims with few false alarms.
- **Sampling changes calibration.** A 50/50 sample of a ~17%-base-rate population makes a well-calibrated judge look underconfident. Calibration tests need samples at the production base rate, or reweighting.

## Findings, round 4: a neutral review panel (2026-09-24)

Details: `prototype/review_panel/README.md`. Three context-free Claude subagents (Opus, Sonnet, Sonnet), blind protocol, majority vote.

- **A blind LLM panel is a workable neutral reviewer.** Fleiss kappa 0.76. It replaced a biased upper bound (91%, disputes only, reviewed by the analyst) with an estimate and interval: Jev 95.8% (88.7–97.9%) acceptable vs the answer key's 91.9%. → Product feature: `review --panel` with this protocol (blind first, sources hidden, random audit slice), humans for what the panel splits on.
- **The analyst's bias ran the other way.** Claude's own verdicts matched the panel 6/13, always harsher on Jev. Self-review is unreliable in both directions; this is the argument for the panel.
- **Gold needs acceptable sets, not one label.** 20 of 45 disagreements had two defensible labels. Exact-match accuracy punishes overlap in the taxonomy, not model error. → Gold format: `label` plus optional `also_ok`; test reports exact and acceptable accuracy.
- **Yes/no questions need a way out.** On truncated or ambiguous input, reviewers said "unclear" 10/40 times; Jev, forced to yes/no, answered confidently. → Lint: suggest a no-match or unclear outcome (a `choice` with `unclear`, or a separate presence check) when the state can be incomplete. TypeSafe's own guidance says the same.
- **Trim traces from the end that matters.** `prepare.py` kept the start of long final messages and dropped the claim. Clipping is a semantic decision per field (keep the head of an issue, the tail of a conversation); hunch's trace source should make it explicit.
- **Beware small-sample retractions.** A 40-row subset suggested 26% overclaiming against 39% on the full set; the full set after the fix says 41%. The panel verified the *detector* (97%), not the rate.

## Findings, round 5: Phase 1, trust the numbers (2026-09-24)

- **Stratified estimate replaces the one-sided bound.** `test` splits rows by whether the model agreed with the answer key; reviews of each group (all disagreements, a random audit of agreements) estimate that group; groups are weighted by size. On the BANKING77 holdout with the panel's verdicts imported, hunch reports 95.8% (95% CI 88.7–97.9%), matching the panel's independent scorer exactly. With disputes reviewed and no audit, `test` now refuses to estimate and says why.
- **Accuracy "on current gold" is a trap once disagreements are reviewed.** It jumped to 98.2% because it trusts every unreviewed agreeing row, while the audit found ~3% of those wrong. The estimate is now the headline and the number `min_accuracy` checks. Calibration and auto-acted accuracy still use current gold; same upward lean, open item.
- **Gold is a set.** `both_ok` verdicts give two acceptable labels (20 of 45 BANKING77 disagreements); right means "in the set" everywhere (test, diff, sign test, queue).
- **Asymmetric thresholds are the product for agent evals.** At the real pass rate (16.7%), acting on "yes" is never safe (53–60% wrong at 0.5–0.8), while acting on "no" at 0.80 automates 44% of all runs at 1.9% error (0.90: 27% at 0.6%). `act: {yes: .., no: ..}` and a two-sided dial.
- **Base rate flips the calibration story.** On the 50/50 sample Jev looked underconfident; reweighted to 16.7% it is overconfident about "yes" (stated 0.54 → observed 0.30; 0.74 → 0.47). Never read calibration off a balanced eval set. AUROC is unaffected by base rate (0.831 either way). `tests.<q>.base_rate` reweights accuracy, calibration and the dial; choice questions (per-class rates) still open.
- **YAML's Norway problem bit the spec.** `act: {yes: .9, no: .8}` parsed as `{True: .9, False: .8}`; options named on/off/NO collapse silently (three options became two in a test). Specs now load with only true/false as booleans. Any spec format that is YAML must do this.

## Findings, round 6: Phase 2, the graph (2026-09-24, corrected after an adversarial review)

Primitives added: a **project** (a folder of specs run in dependency order), `source: ref(x)`, `where:` (a safe expression over row columns), `union: [...]`, `reviews:` (share verdicts across judgments), `weights:` (source sampling), `chain:` (refinement confidence). Two recipes were built from them with no recipe-specific code: a BANKING77 tree and `recipes/agent_eval`.

An independent reviewer (Fable, no shared context, `--max-cost 0`) found real bugs and two overstated claims in the first version of this section. Corrections are folded in below; what changed is marked.

- **The graph works; the tree loses.** Coarse group → fine intent vs one flat 77-way question, BANKING77 holdout, same descriptions. Paired on reviewed gold: tree fixed 3, broke 35 (p < 0.001; on the raw answer key 8 vs 32, same direction). Estimated accuracy: flat 95.8% (88.7–97.9%), tree **87.5% (80.0–89.8%)**. *Corrected:* first reported as 89.1%; that extrapolated from reviews made for the flat model, and the tree's 24 unreviewed disagreements turned out to be tree errors on 19 and both-acceptable on 5 (blind panel, 19/24 unanimous). The tree is 48% cheaper ($0.019 vs $0.036), needs two sequential calls, and sends 33 of 385 rows to a group that doesn't contain their answer.
- **Confidence can be chained, but only for refinements.** *Corrected twice.* (1) The first version multiplied by the upstream *top-label* confidence and re-multiplied it at every later hop; it now multiplies by P(the where-clause holds) under the upstream answer's full distribution, once per hop (reviewer's toy graph: 0.64 expected, 0.2 before, 0.64 now). (2) Chaining is right when the downstream answer can only be correct if the row was routed correctly (the tree's fine intent). It is wrong for a gating filter: whether a patch passes its tests doesn't depend on whether the agent claimed it did. So it is opt-in, `chain: true`. *Corrected claim:* chaining's calibration gain (0.069 → 0.021) was mostly uniform deflation (a constant factor gives 0.027); the real evidence is that chained confidence separates right from wrong answers much better, AUROC 0.797 → 0.897. `test` now reports that.
- **Reviews must record why they were made.** The estimator assumes reviewed rows are a random sample of their group. Reviews made for another judgment are not: the tree's estimate inherited the flat model's disputes, and then the tree's disputes inflated the flat model's agreeing group (95.8% → 96.6%). Reviews now carry `kind` (audit / disputed / uncertain); only audits stand in for unreviewed agreeing rows, and an estimate is refused until every disagreement is reviewed.
- **Sampling weights belong to rows, not questions.** Phase 1's per-question `base_rate` was wrong downstream: runs that claim a fix pass ~24% of the time, not 16.7% (reviewer confirmed 24.4%). `weights: {by, population}` on the source gives each row `_w`, which flows through `ref()`.
- **One store per workspace.** Per-folder stores made a cross-project diff re-ask 385 answers ($0.036) and an earlier fix pointed an old spec at the wrong folder ($0.017). The store is the nearest `.hunch/` up the tree, else the git root. Content addressing then paid off across projects: agent_eval's `fix_correct` was entirely cached from `patch_eval`.
- **Say what it costs before spending.** Every fill prints what it will ask and the estimated cost; `--max-cost` refuses above a cap (the reviewer ran everything at `--max-cost 0`). `compile` on a conditional graph gives only a loose upper bound (10× for the tree).
- **Names that collide must be errors.** A question named like its gold column overwrote gold in materialized rows (caught when a crosstab looked wrong); a judgment named `answers` would have dropped the cache; two specs with the same name silently overwrote each other; overlapping union branches duplicated rows. All four are now lint or load errors.
- **Diff across the graph shows upstream effects.** Removing `unclear` from `claims` flipped 8 claims; downstream, 3 runs newly reached `fix_correct`/`verified` and 1 left, "own spec unchanged: moved by upstream changes"; only 3 new answers were paid for. A renamed question is matched by its cache keys.
- **Repetitive graphs need generation.** The tree is 12 near-identical specs from `build.py`. A Python API (Pydantic classes, see 03) or templating is needed.
- **agent_eval, corrected:** with `fix_correct` on its own confidence (not chained), auto-reject at 0.80 handles 23.3% of claimed runs at 3.4% error (0.90: 9.3% at 0%). *First reported as 18.8% at 1.4%, from the wrongly chained confidence.* Verified-before-claim runs pass 28.9% vs 17.8% (population mix; Fisher p = 0.079, suggestive; `verified` has no gold).
- **Still open from the review:** a weighted version of the stratified estimate; routing accuracy for `group` needs gold derived from `gold_intent` (the misroute count is the proxy); cleanup of `_stats` done, `compile`'s upper bound for conditional graphs not.

## Findings, round 7: does the agent's claim sway the judge? (2026-09-24)

`patch_only.yml` = `patch_eval`'s `passes_tests` with `final_messages` removed from the state; same instructions, one variable changed. 200 SWE-agent runs, $0.008. The original was also re-asked once (by accident, below), which gives the noise floor.

| Δ p(passes), with messages − without | mean (95% CI) | re-ask noise |
|---|---|---|
| agent claims a fix, patch fails (n=66) | −0.027 (±0.028) | +0.005 |
| agent claims a fix, patch passes (n=94) | −0.011 (±0.020) | −0.003 |
| no claim, patch fails (n=34) | −0.067 (±0.049) | +0.003 |

- **No claim contagion.** The messages matter (mean \|Δp\| 0.084 vs 0.013 for re-asking), but "I fixed it" does not push Jev toward "passes"; "I couldn't" pushes it toward "fails", correctly (34 of 40 such patches fail). Ranking is set by the patch: AUROC 0.831 with messages, 0.828 without, 0.836 re-asked. Caveats: one dataset, claim labels are Jev's (97% vs the panel), small no-claim group.
- **Re-asking noise is small here:** mean \|Δp\| 0.013, flips concentrated in the 0.4–0.6 band (accuracy 73.5% vs 74.5% between two runs of one spec).
- **Two silent failures, fixed.** (1) A spec outside the workspace got a fresh empty store, and `diff` gave the old spec the same one, so 400 cached answers were re-asked ($0.012; `--max-cost` is per judgment and didn't stop it). Creating a store now prints where and why. (2) `diff` paired judgments by name only, so `patch_only` vs `patch_eval` compared nothing and printed nothing; a one-judgment project now pairs with the other side's only judgment, and no names in common is an error.

## Findings, round 8: shadow mode (2026-09-24)

Change a live judgment safely: the app keeps answering with the live spec while a candidate answers the same rows on the side; then compare them on that real traffic. Built from existing parts, no new command:

- `judge(LIVE, shadow=CANDIDATE, **row)`: both answer concurrently; the app gets only the live answer; a failing candidate is logged to stderr, never raised. The row goes into a `traffic` table in the store (one entry per distinct row and judgment name, a repeat counter), under both specs' root names.
- `--traffic` (like `--source`): the root judgments read the logged rows, exported to `.hunch/traffic/<judgment>.csv`. Rows without a key get one from their content (`t<hash>`), stable across exports, so reviews stay attached.
- The shadow report is `hunch diff CANDIDATE --against LIVE --traffic`: every answer is already cached, so it is free.
- "Which side is right" needs gold only where they differ (the paired sign test ignores rows that agree). `review --against` queues exactly those first, as `kind=shadow` with verdicts `against_right` / `spec_right` / `both_ok`. The estimator treats them like disputes: they count for their own rows, never extrapolated (only audits are).
- Demo (`examples/banking77/shadow_demo.py`, $0): live = flat intent spec, candidate = the tree, 40 holdout texts as traffic: 8 differ.
- Fixed on the way: `diff` said "accuracy on the 40 shared rows" when 3 had gold; it now counts rows with gold (never visible before because every dataset had gold on every row).
- Not done: the candidate adds latency (the slower of the two, not fire-and-forget; an `asyncio.run` per call can't outlive the call); traffic is logged only when shadowing, so a candidate written later can't be replayed on past traffic; logged rows are raw inputs kept in the local store (redaction, 06, applies here too).

## Findings, round 9: real developer ↔ agent conversations (2026-09-24)

The first-user workload for real: 28 Claude Code sessions from Trace Commons (CC BY 4.0, real developers, public repos) → 309 turns (`examples/claude_code/prepare.py`: request, the agent's final reply, the developer's next message; `edits` and `ran_after_edit` computed in code). Two judgments, $0.020:

- `outcome` (from the developer's next message: worked / failed / redirected / unclear): 189 / 75 / 32 / 13.
- `claims_done` (from the final reply only, so it can run live): does the agent say the work is done?

**Checked by a blind panel** (3 reviewers, 60 random turns, same definitions, no model answers shown; `review_panel/claude_code/`): `outcome` 90% (95% CI 80–95%), `claims_done` 88% (78–94%); reviewers agree with each other at kappa 0.88 / 0.86. Jev's `outcome` errors lean one way: 5 of 6 say "worked" where the panel saw failed or redirected, so failure rates below are, if anything, low. At act = 0.80 `outcome` labels 77% of turns at 2.2% error.

**Does running something after editing matter?** Among turns where the agent edited files and claimed it was done:

| after the last edit, the agent… | turns | the developer reports it didn't work |
|---|---|---|
| ran nothing | 142 | 33% |
| ran a command | 31 | 16% |

Fisher p = 0.045 per turn, but turns cluster in 23 sessions: a session bootstrap puts the gap at +17 points, 95% CI −1 to +30. Suggestive, like agent_eval's `verified` (p = 0.079), not proven; "ran a command" is any Bash call, not necessarily a test.

**Gold only from reviews broke `test`** (the normal case for live traffic and any new judgment): the estimator assumed an answer key, reported "0.0%, PASS" and then crashed. Now: with no key, random audits estimate every row (hits / audited, Wilson interval), and `review` draws its audits from every row. The audit quota counts only `audit` reviews (the Phase 2 fix already assumed this). BANKING77 estimates unchanged (95.8%, 87.5%).

**Reading the format:** Claude Code 2.1 has no `origin` field, so a human message is a user message that is not a tool result, meta, a sidechain, a compaction summary, or wholly harness-injected tags (IDE selection, `!` shell I/O, reminders); `promptSource: sdk` turned out to be people in the VS Code extension. An interruption is a reaction, not a request.

## Findings, round 10: a second engine (2026-09-24)

**Is hunch more than a Jev wrapper?** The same specs, unchanged, on an LLM: `model: deepseek:deepseek-flash` (DeepSeek V4.1 Flash) or `--model` on any command. The engine asks one request per question with the options numbered, and reads the probabilities of the first answer token (top-20 logprobs, renormalised over the options), so answers have Jev's shapes and every test, dial, diff and review works as before. The model string and an adapter version (`logprobs-v1`) are part of the cache key.

| | Jev (jev-1.13.0) | DeepSeek V4.1 Flash |
|---|---|---|
| BANKING77 holdout, estimated accuracy | **95.8%** (88.7–97.9%) | 92.3% (85.8–93.5%) |
| paired, reviewed gold | | fixed 4, broke 22, **p = 0.001** |
| calibration error / auto-acted at 0.9 | 0.046 / 100% on 81% of rows | 0.032 / 97.7% on 91% |
| SWE-agent patch passes tests (AUROC) | 0.831 | **0.866** (fixed 22, broke 17, p = 0.52) |
| Claude Code `outcome` / `claims_done` (panel audits) | 90.0% / 88.3% | 88.3% / 88.3% (n.s.) |
| cost of these four judgments | $0.068 | $0.20 (list price; cache hits and off-peak are cheaper) |
| online latency, new text | ~600 ms | ~750 ms |

DeepSeek's 13 BANKING77 disagreements that no one had reviewed (Jev agreed with the key there) went to the same blind panel: answer key right on 12, DeepSeek on 1 (`review_panel/deepseek/`, appended by `disputes.py deepseek`).

- **Engine-neutral in practice.** No spec changed; the verdict per task came from hunch's own tests: Jev is significantly better on the 77-way intent task, the LLM is level or better (not significantly) on yes/no judgments over long text, at ~3× the cost. That is the kind of decision hunch exists for.
- **Logprobs are fragile across providers.** OpenRouter lists logprobs for many providers that don't return them (Novita) or reason despite "reasoning off" (Wafer on GLM, which then ran out of room); Straitly and Z.ai return none. At temperature 0 DeepSeek's API masks every other token (-9999), so the engine reads at temperature 1: only the probabilities are used, never the sampled text. Pin a provider with `openrouter:<id>@<provider>`; the provider is part of the model string, so of the key. GLM-5.3-flash works pinned to Parasail but was not compared: the OpenRouter account ran out of credit (only you can top it up).
- **Tables per engine.** `run --model X` writes `<judgment>__<engine>` tables; the spec's own tables stay as they were (the first LLM run overwrote them).

## Lessons from dlt (prior art, see 03 related work)

Decisions for the real build:

- **Lineage on every materialized row**: `_hunch_run_id`, `_hunch_key` (answer key), plus a `_hunch_runs` table (run id, spec hash, git sha, model, engine, cost, status: running/complete/failed). Downstream (dbt) reads only complete runs; any label traces to the exact spec version that produced it.
- **Spec-change policy, named like dlt contracts.** Per judgment: `on_change: reask` (default: every row, exact but costs a full run) | `new_rows_only` (old rows keep old labels; cheap, inconsistent, recorded in lineage) | `freeze` (refuse to run a changed spec without `--allow-change`). The diff already shows the cost of `reask` before you pay it.
- **Cursor + hash for big sources**: optional `incremental: {cursor: updated_at}` to limit which rows are read and hashed; the content key still decides whether to ask.
- **Library first**: `hunch.run("intent.yml")`, `hunch.results("intent").df()`; CLI calls the library. No daemon required for anything in the Apache layer.
- **Agent-native**: ship a skill/MCP so coding agents write specs and iterate with `lint` → `test` → `diff` as their feedback loop.

### Resolved open questions

- Order stability costs N× calls → run on a deterministic sample (stable across runs, so cached). 150 rows × 2 permutations = $0.03.
- Score calibration still open (ordinal); noul calibration implemented, untested on real gold.
