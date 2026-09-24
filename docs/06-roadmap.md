# 06 — Roadmap (draft, 2026-09-24)

Ordered by risk: the things we don't know yet come before the things we only have to build. Each item names the question it answers and how we'll know. Costs are API spend at jev-1.13.0 prices.

## Product principles

These decide between options at every phase. They come from why dbt and dlt won: not one feature, but small pieces that compose into recipes people share.

1. **Composable, not a single question.** A few primitives (source, judgment, test, route, review, sink) snap together into recipes. Judgments `ref()` sources, other judgments, and dbt models. No feature may exist only for one use case.
2. **Recipes are the unit of reuse.** A recipe is a folder of specs, tests and gold that someone else can install and adapt, like a dbt package or a dlt verified source. The first one is an agent-eval recipe for our first user.
3. **Lineage everywhere.** Every answer can say which spec version, model, run, and upstream answers produced it.
4. **Don't build connectors.** Data comes in through dlt (any dlt resource is a hunch source) or dbt (any model is a hunch source). hunch's job starts at "rows exist".
5. **Library first, then CLI, then server.** Import it anywhere (notebook, app, CI, agent); the CLI is a thin shell; the server is optional.
6. **The first ten minutes decide adoption.** `hunch init <recipe>` → a working example with sample data → `run`, `test`, `diff` on your own rows. Errors say what to do.
7. **Built for coding agents too.** Specs are plain files; `lint → test → diff` is the feedback loop an agent uses to improve them.

## Where we are

Proven (see `prototype/README.md`, docs/04-design.md rounds 1–14), updated 2026-09-24: content-addressed store shared by batch, online and server; lint; calibration / AUROC / dial / order tests; audit-based accuracy estimates (weighted, or from random audits when there is no answer key); backtest and cross-engine diff with a significance test; review queue (CLI and web); judgment graphs; two engines (Jev, LLMs via logprobs) with escalation between them; trace, Python and CSV sources with redaction; shadow mode and replay; `suggest` gated on held-out gold; lineage and spec-change policies; a package with Pydantic classes as specs; a server (ELv2). Validated on intent classification (BANKING77, flat and tree), agent-run evals (SWE-agent), real developer ↔ agent conversations (Claude Code) and 100k reviews at scale. Rounds 6 and 14 were independent adversarial reviews; every finding was fixed. Total API spend so far: about $2.76, $1.55 of it the 100k-row scale test.

**Not proven yet:** whether anyone besides us wants this (no external user yet), a shared multi-worker store (Postgres), and a published package name (`hunch` is squatted on PyPI).

## Phase 0: decide who it's for (no code)

| Question | How we'll know |
|---|---|
| Who is the first user: analytics engineer (labels in the warehouse), AI engineer (evals over traces), or ops (routing rules)? | One named persona, one real workload they'd run weekly, written down. |
| What is that workload? | A dataset we can use, ideally with some gold labels, and a person who would review the queue. |

Everything below gets prioritized against that workload.

**Decided 2026-09-24: the AI engineer comes first** (evals over agent and LLM traces), then analytics engineers, then ops. Why: the SWE-agent result (39% of claimed fixes fail; confident "no" is 51/53 right) is the strongest evidence so far, and that audience feels the pain now. Still open: the named real workload and a reviewer.

Consequences for the phases below: trace ingestion (OpenTelemetry GenAI conventions) moves up from Phase 5 into Phase 3 as a batch source; asymmetric yes/no thresholds (Phase 1) and conditional nodes (Phase 2) matter most, because agent evals are mostly yes/no checks chained on earlier answers.

## Phase 1: trust the numbers: DONE 2026-09-24 ($0 new API spend)

| Item | Result |
|---|---|
| Random audit slice | `review --audit N` mixes a fixed random sample of agreeing rows into the queue; `test` reports a stratified estimate with a 95% CI and refuses to estimate from disputes alone. Reproduces the panel's 95.8% (88.7–97.9%) exactly. |
| Asymmetric yes/no thresholds | `act: {yes, no}` + two-sided dial. Agent evals: auto-reject 44% of runs at 1.9% error; auto-accept is never safe. |
| Calibration at the production base rate | `base_rate` reweights; SWE at 16.7% shows Jev **over**confident on "yes" (the 50/50 sample made it look under). |
| Gold as acceptable sets | `both_ok` verdicts; 20 of 45 BANKING77 disagreements. |

Also found: YAML's Norway problem (yes/no parsed as booleans), fixed in the spec loader. Open: calibration and auto-acted accuracy under review-corrected gold still lean upward; per-class base rates for choice questions.

## Phase 2: the graph and the first recipes: DONE 2026-09-24 (~$0.10 API; corrected after an adversarial review, see 04 round 6)

| Item | Result |
|---|---|
| `ref()` between judgments | Projects (folders of specs), `source: ref(x)`, dependency order, cycle and unknown-ref errors, graph-aware lint. |
| Hierarchical vs flat (BANKING77) | Flat wins on accuracy (95.8% vs 87.5% estimated; 35 broken vs 3 fixed, p < 0.001); tree is 48% cheaper. Chained confidence (opt-in `chain: true`) separates right from wrong far better (AUROC 0.80 → 0.90). |
| Diff across a graph | Upstream change → per-judgment flips plus rows entering/leaving, flagged "own spec unchanged". |
| Conditional nodes | `where:`; skipped rows cost nothing (agent_eval: 149 of 200 reach the checks). |
| First recipe: agent-eval | `recipes/agent_eval`: claims → fix_correct / verified, with weights, tests, review (gating filters, so not chained). Claude Code conversations: `examples/claude_code` (04 round 9), read by a script until the Phase 3 trace source. |
| Generality check | The tree (routing + union) and agent_eval (conditional checks) used only general primitives. New general pieces the recipes forced: chained confidence (refinements only), source weights, review `kind`, one store per workspace, name-collision errors. Gap: repetitive graphs need a generator (Python API or templating). |

## Next up, from Pydantic AI's TypeSafe integration (read 2026-09-24, see 03 related work)

Cheap, specific, and mostly prototype-sized; fold into Phases 3–4.

| Item | Why | Done when |
|---|---|---|
| ~~Ablation: does the agent's own claim sway `fix_correct`?~~ DONE | "Adversarial text can move Jev"; the state includes the agent saying it fixed it | **No contagion** (04 round 7): the messages move answers 6× more than re-asking does, but a claim doesn't raise p(passes) and AUROC is unchanged (0.831 vs 0.828). Done as a spec copy + `diff` (`examples/swe_agent/patch_only.yml`), $0.008 |
| ~~"None of these" as a primitive~~ DONE (04 round 11) | Declining should be a choice, not low confidence; agent_eval hand-built `unclear` | `none: "<when>"` adds a `none_of_these` option (sent, keyed); `test` reports the declined rate |
| ~~Multi-label questions~~ DONE (04 round 11) | Pydantic's `list[Literal]` fans out to one yes/no per option | `type: multi` expands to one noul per option, tested and diffed per option; combined column and exact-set accuracy |
| ~~State-size lint~~ DONE (04 round 11) | 32k tokens for state + longest question; past it the request fails | `compile`/`run` warn at 80% of the limit, name the largest column and suggest `clip:` |
| ~~Redaction for traces~~ DONE (04 round 11) | State leaves the machine; traces carry secrets and customer data | `redact: [secrets, emails, home, <regex>]` before hashing and sending, and on rows logged for shadow/replay; `clip:` per column (head or tail) |
| ~~LLM escalation tier~~ DONE (04 round 11) | Pydantic's `FallbackModel` on low confidence | `escalate: {model: X}` re-asks only answers below `act` on engine X; `test` scores the combined system and reports how many were escalated |
| ~~Confidence vocabulary~~ DONE | Pydantic reports a margin, abs(p − 0.5) × 2; hunch reports a probability | `judge()` returns both: `p` and `margin` |

## Next up, from OpenServ's SERV / Graph Sharding (read 2026-09-24, see 03 related work)

| Item | Why | Done when |
|---|---|---|
| ~~Shadow mode~~ DONE (04 round 8) | OpenServ's decision nodes have a "Shadow" tab; the safe way to change a live judgment is to run the candidate beside it first | Built without a new command: `judge(..., shadow=...)` logs the row and caches the candidate's answer; `--traffic` makes `diff` the shadow report and `review --against` queue only the rows where they differ. Since round 11: the candidate runs after the live answer returns (no added latency), and `judge(..., log=True)` keeps rows so a candidate written later can be replayed with `--traffic` |
| ~~`hunch suggest`, gated by `diff`~~ DONE (04 round 12) | SERV's pitch is clearer instructions make Jev better; our own biggest gain was option descriptions (82% → 88%). Rewrites are only worth keeping if measured | `suggest` drafts question/option rewrites (from example rows and confusions); each is kept only if `diff` on gold shows a significant gain; the report says which were rejected. Built with a held-out half and Bonferroni; the first kept rewrite shrank from +5.1% to +2.4% (n.s.) on the holdout, so confirmation on a holdout stays a required step |
| Runtime adapters (Pydantic AI DONE, 04 round 12) | Decision nodes now live inside runtimes (Pydantic AI output types, SERV graphs); hunch should test the same definition that runs | Import a Pydantic AI output type as a spec (`hunch.spec_from_model`, classes and bare types; `to_model` back); import SERV decision nodes if they expose definitions (open: they don't publish a format); export a hunch spec back where possible |
| Decision inputs written by an LLM | SERV decision nodes judge LLM-written summaries; agent_eval judges the agent's own claims | The claim-contagion ablation (above) generalised. It worked with no new code (copy the spec, drop the column, `diff`), but `diff` reports label flips and accuracy, not *how far and which way* probabilities moved, or the re-ask noise floor; those came from a throwaway script. Add them to `diff` when a second ablation needs them |
| Watch list | Measurement features may arrive inside runtimes | Track OpenServ Graph Sharding / Shadow Agents / Benchmark Tooling and Pydantic's evals library; re-check each quarter whether they add gold-based measurement |

## Phase 3: engine independence and scale (prototype, ~$0.50–2)

| Item | Question it answers | Done when |
|---|---|---|
| ~~Second engine adapter~~ DONE (04 round 10) | Is the spec really engine-neutral? | `deepseek:<id>` / `openrouter:<id>[@provider]` through answer-token logprobs; `--model` swaps the engine for any command. |
| ~~Cross-engine diff~~ DONE (04 round 10) | Which engine should this judgment use? | `diff SPEC --against SPEC --model X`: flips, fixed/broken on gold, sign test; `test --model X` for calibration and cost. |
| ~~Scale test at 100k rows~~ DONE (04 round 14) | Where does it break: hashing, store size, rate limits? | 57.5 requests/s at 32 in flight, no 429s (the documented 1,200/min wasn't enforced), 0.03% transport retries, $1.55, +80 MB; a cached re-run takes 4 s; `test` fixed from 156 s to 5 s (AUROC was quadratic). |
| ~~Cursor + hash incremental~~ NOT NEEDED YET (04 round 14) | Can we avoid re-reading an unchanged big table? | Measured: hashing 100k rows and looking them up takes ~3 s, so a cursor saves nothing until tens of millions of rows. Revisit with a real table that size. |
| Trace source via dlt (moved up for the AI-engineer persona) | Can hunch read agent traces directly instead of a hand-built CSV? | Any dlt resource is a hunch source; demonstrated with an OpenTelemetry GenAI / Langfuse-style export. Per-field trimming is declared in the spec (keep the head of an issue, the tail of a conversation), not hand-coded like `prepare.py`. **Round 11:** `source: traces(<glob>)` reads Claude Code, Cursor, OpenCode and OpenTelemetry GenAI files (`traces.py`), `source: py(file:fn)` takes any function returning dicts (a dlt resource included), `clip:` is in the spec; the Claude Code example dropped `prepare.py` with identical keys. |

## Phase 4: package v0.1 (product, Apache 2.0)

Known engineering; do it once phases 1–3 have settled the shapes.

- ~~Library first, Pydantic classes as specs, Python-generated graphs~~ DONE (04 round 12; `results()` returns dicts, not a DataFrame). **Library first**: `hunch.run()`, `hunch.judge()`, `hunch.results("x").df()`; CLI is a thin shell. **Pydantic classes as specs**: accept Pydantic AI's type mapping (bool / Literal / Enum / IntEnum rubric / list fan-out / Optional) so the output type that runs live in an agent is the one hunch tests and diffs; YAML remains the stored form. A Python API that generates graphs (the BANKING77 tree needed `build.py` for 12 near-identical specs).
- **Cost guard**: every command states what it will spend; `--max-cost` refuses above a cap (prototype has both); `compile` estimates for conditional graphs from past pass rates instead of a loose upper bound.
- **Spec format**: JSON Schema, versioned; lint built on it.
- ~~Lineage~~ DONE (04 round 12; per-question `<qid>_key` columns). **Lineage**: `_hunch_run_id` and `_hunch_key` on every materialized row; `_hunch_runs` table (spec hash, git sha, model, cost, status). Downstream reads complete runs only.
- ~~Spec-change policy~~ DONE (04 round 12). **Spec-change policy**: `on_change: reask | new_rows_only | freeze`; `diff` shows the cost before you pay it.
- **Stores**: SQLite (local), Postgres (shared), selected by one string.
- **dbt interop**: read `manifest.json` as sources; emit dbt `sources:` YAML for materialized judgments.
- **Agent skill / MCP**: coding agents write specs and iterate with lint → test → diff.
- ~~Recipes as packages~~ first step DONE: `hunch init agent-eval` from the package (no `add`, no hub, no versioning yet). **Recipes as packages**: `hunch init agent-eval`, `hunch add <recipe>`; a recipe hub later. Recipes are versioned, have their own tests and gold, and are overridable (change a threshold or a question without forking).
- **UX pass**: first-ten-minutes path timed with a new user; every error message says what to do next; a Python API (decorators, like dlt) that produces the same spec as the YAML.
- **dlt interop**: dlt resources as sources; hunch results loadable by dlt to any destination.
- **Run-level checks (built in, need run history)**: label-distribution drift vs the previous run, review-rate ceiling, confidence drift, cost budget per run. Generic checks on the output table (nulls, accepted values, ranges) are *not* built: documented as Great Expectations / Soda / dbt tests pointed at hunch's table.

## Ideas borrowed from Great Expectations (UX, not integration)

GX is a reference for what makes quality checks *useful to people*, not a dependency. Decided 2026-09-24: no GX integration unless a user needs it. What to borrow:

- **Named, readable checks.** GX's "expect_column_values_to_be_between" reads like a sentence a non-engineer can review. hunch tests should read the same way in the spec and in reports ("expect accuracy ≥ 90% on the holdout", "expect ≤ 15% sent to review").
- **Docs generated from results.** GX's Data Docs turn every validation into a browsable report. hunch equivalent: `hunch report` writes one shareable page per run (dial, calibration, confident mistakes, diff, lineage), like the field-report artifact but generated.
- **Checkpoints.** A named bundle of "which data, which checks, what to do on failure" (fail CI, notify, open review queue). hunch equivalent: a `checks:` block per recipe that CI and schedules run.
- **Profiling to get started.** GX can propose checks from a sample. hunch equivalent: `hunch suggest` proposes thresholds from the dial, flags bare options, and drafts option descriptions from example rows, so a new user starts from a reasonable spec.
- **A gallery of checks.** Browsable, documented, reusable. hunch equivalent: the recipe hub, with tests included.

Parked: a GX custom expectation / dbt generic test backed by hunch (`expect_column_values_to_satisfy("company_name", "is a real company")`). Revisit only if users ask for it.
- **Release**: PyPI name decided (`hunch` is a squatted placeholder), repo public, docs site.

## Phase 5: server (ELv2): first version DONE locally (04 round 13; `server/`)

Only after v0.1 has users.

- Review UI (the queue, audit slice, "answer key is wrong"), multi-reviewer, agreement stats. *Done: the queue with one-click verdicts, reviewer name per verdict. Open: accounts, agreement stats.*
- Online serving with a shared store and background writes. *Done: `/v1/judge` with shadow/log, candidate in the event loop. Open: several workers, Postgres.*
- Live trace ingestion for evals on production traffic (the batch trace source lands in Phase 3).
- Monitoring: label drift, calibration drift, cost per judgment over time. *Done: label mix per run with a drift flag, runs with cost. Open: calibration drift (needs gold over time).*

## Parked (revisit with evidence)

- Score-question calibration (ordinal); `suggest-rules` (demote a judgment to a rule when a rule agrees on ≥X% of gold); multi-reviewer consensus; per-row noise estimates by resampling.
