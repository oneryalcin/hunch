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

## Next up, from Pydantic AI's decision models (v2.50, read 2026-09-25, see 03 related work)

Pydantic AI 2.50 generalises Jev into a `DecisionModel` interface and uses decision models inside agent runs (model routing, tool-call guards, tool preselection). Its own docs say those in-run classifiers need measuring and that "nothing in the run will tell you". Position unchanged and sharpened: **hunch does not execute routes, tools or hooks; it measures the decisions made there.** Pre-v1, in this order:

| Item | Why | Done when |
|---|---|---|
| ~~Correct the parity claim~~ DONE 2026-09-25 | `spec_from_model` said it maps a class "the same as Pydantic AI". Since 2.50 Pydantic AI sends each question as structured parts (`field` name, class docstring as `goal`, description as `question`, agent `instructions`, `BoolCriteria`); hunch sends the description alone, so the same class can get different answers | `models.py` docstring and the Use-in-your-app guide say what differs |
| `hunch.spec_from_agent(agent)` + a `pydantic:` engine | The class that runs live must be the class that is tested, word for word. Reimplementing their ~2,000 lines of question building would drift every release | Questions captured from Pydantic AI itself with a recording `DecisionModel` (no API call; probe written 2026-09-25) and stored as structured `instructions` (the spec already accepts them). `model: pydantic:<module>:<Class>` runs any `DecisionModel` through hunch's `ask(state, questions)`, which already has the same shape as `decide`. Optional extra; core stays `httpx` + `pyyaml`. Blocked until the uv cooldown admits 2.50 (~2026-09-30) |
| ~~Cookbook: guard a coding agent's tool calls~~ DONE 2026-09-25 (04 round 16) | Their headline in-run use, and the one most in need of measurement; we already hold the data (Trace Commons, the maintainer's sessions as aggregates) | A spec over tool calls ("safe to run without a person looking?"), gold from review, `act` from the dial, then `hunch.judge()` in a hook. Publishes only if the numbers are honest |
| Read Pydantic AI `decide` spans as rows | With tracing on, every decision request records the questions as sent, the state and the answers with probabilities. Hooks multiply decisions per run; this is how hunch sees them with no code in the app | A trace `view` over OpenTelemetry `decide` spans: review, `test` and shadow on real traffic. Build when a Pydantic AI user (or we) run one |
| Parked: hunch's LLM engine as a Pydantic AI `DecisionModel` | Any LLM read through token probabilities as a decision model (measured: 92.3% vs Jev's 95.8% on BANKING77). A distribution channel, but an engine is a different product from measurement | Revisit if asked, or offer upstream |
| Rejected | Pydantic AI as hunch's core engine dependency (API changed within a week, cache keys would move with their releases); implementing routes, tools, hooks, streaming or compaction (a second, weaker agent framework) | |

## Next up, from dbt (docs index read 2026-09-25, [llms.txt](https://docs.getdbt.com/llms.txt))

**Lesson.** dbt did not spread through features. It spread through four things: a file format anyone can read, **machine-readable artifacts** that others built on ([`manifest.json`](https://docs.getdbt.com/reference/artifacts/manifest-json.md), [`run_results.json`](https://docs.getdbt.com/reference/artifacts/run-results-json.md); its docs site, [state comparison](https://docs.getdbt.com/docs/deploy/dbt-state-about.md), orchestrators and observability tools all read them), [packages](https://docs.getdbt.com/docs/build/packages.md), and [docs generated from the project](https://docs.getdbt.com/docs/explore/build-and-view-your-docs.md). Its plugin API (adapters) exists because warehouses differ. hunch should copy the four, not the adapter system.

**Already covered by design.** `state:modified`, deferral and retry exist so dbt does not redo work; hunch's store keys every answer by its exact input, so a re-run asks only what changed and a retry is free. Slim CI is the cached store (guides/test-in-ci). `compile`, `init` recipes, lineage columns (`_hunch_run_id`, `<qid>_key`, `_hunch_runs`) and dlt / `py()` sources exist.

Pre-v1, in this order (each one PR):

| Item | dbt reference | Why for hunch | Done when |
|---|---|---|---|
| ~~**Artifacts**~~ `results.json` DONE 2026-09-25: `test` writes `.hunch/target/<tested path>.json` (removed at start, written only when `test` finishes); `manifest.json` deferred until `hunch docs` needs it (docs can read specs directly) | [dbt artifacts](https://docs.getdbt.com/reference/artifacts/dbt-artifacts.md), [run_results.json](https://docs.getdbt.com/reference/artifacts/run-results-json.md), [manifest.json](https://docs.getdbt.com/reference/artifacts/manifest-json.md) | Everything below reads them: docs, metrics, CI annotations, dashboards, agents. Today results exist only as terminal text and store tables | `run`/`test`/`diff` write versioned JSON to `.hunch/target/`: `results.json` (per question: accuracy estimate + interval, dial, calibration, AUROC, review coverage, checks with pass/fail, cost, model, spec hash, git sha) and `manifest.json` (resolved specs, questions as sent, state columns, lineage edges). A JSON Schema per file; stable field names from v1 |
| ~~**Metrics with intervals**~~ DONE 2026-09-25: `metrics: {name: {rule: …}}` in the `where` language; `test` reports the rule on answers (exact), on gold (census or random spot checks, Wilson), missed and false alarms; `min_rate`/`max_rate`/`max_missed`/`max_false_alarms`; in results.json. Not yet: gold across judgments (a spec that `ref`s another can use its answers in a rule, but only this judgment's own questions get gold substituted), sampling weights | [MetricFlow](https://docs.getdbt.com/docs/build/about-metricflow.md), [ratio metrics](https://docs.getdbt.com/docs/build/ratio.md) | Twice this week a number was computed by hand from answers, and both needed an honest interval: "agent said done → next message reports failure" (04 round 15) and the command guard's "stop if any question says yes" miss rate (04 round 16). A SQL query cannot give the interval, and hunch can | A `metrics:` block per judgment: counts, rates and ratios over answers with filters (`where`-style expressions over answers and columns), including combined rules (`any_yes: [destroys, reaches_outside, sends_out]`). Reported by `test` and in `results.json` with a 95% Wilson interval when computed over gold, and a plain count over all rows otherwise; `min_`/`max_` checks on them. Not a semantic layer: no dimensions, time spines or BI connectors |
| ~~**Golden examples**~~ DONE 2026-09-25 as a top-level `examples:` list (one row pins several questions, so not per question): `{name, row, expect}`, one check each, in results.json. Not yet: `diff` naming examples a candidate breaks; `severity` (next item) | [unit tests](https://docs.getdbt.com/docs/build/unit-tests.md) | Statistical tests say how often; they cannot pin known edge cases. Must-pass rows catch regressions on the cases that matter (`rm -rf ~` must be `destroys: yes`; the `bun` process kill must be `reaches_outside: yes`), cost a handful of answers in CI, and document where a policy draws its lines | `tests: <question>: examples: [{row: {...state columns}, expect: <label>}]` (inline rows, same redaction and keys as batch); `test` reports each failing example by name; `diff` shows examples a candidate breaks. `severity` applies |
| ~~**Test severity**~~ DONE 2026-09-25: `severity: warn` per test entry, metric or example; also lint: limits must be shares in [0, 1] | [severity, warn_if, error_if](https://docs.getdbt.com/reference/resource-configs/severity.md) | Some checks should inform, not fail the build (a calibration drift while accuracy holds) | `severity: warn` per check or per question; `WARN` lines, exit 0 |
| ~~**Sample runs**~~ DONE 2026-09-25: `--sample N` on compile/run/test/diff, root rows by key hash; `run --sample` caches but never replaces tables; `sample` in results.json | [`--sample`](https://docs.getdbt.com/docs/build/sample-flag.md) | Iterate on a spec over 50 rows before paying for 50,000; today only `compile` (no answers) or the full run exist | `run`/`test`/`diff --sample N`: a deterministic sample by row-id hash, so repeated samples reuse the store |
| ~~**JSON Schema for specs**~~ DONE 2026-09-25: `src/hunch/spec.schema.json` (draft-07), published at its raw GitHub URL (the repo went public 2026-09-25) and pointed to from each recipe spec's first line; also in the package for offline use; check.py fails if its keys or allowed values drift from the code. Lint unchanged (it checks more than a schema can) | [static analysis](https://docs.getdbt.com/docs/build/about-static-analysis.md), [VS Code extension](https://docs.getdbt.com/docs/about-dbt-extension.md) | A schema gives completion, validation and hover docs in VS Code / Cursor through the YAML language server (most of what dbt's language server offers, for a fraction of the work), and makes coding agents write valid specs | Published schema, versioned with the spec format; `# yaml-language-server: $schema=…` in recipes; lint checks against it first, then the semantic rules it cannot express |
| ~~**Agent skill in the package**~~ DONE 2026-09-25: `src/hunch/skills/hunch/SKILL.md` (Agent Skills format, validated), installed by `hunch skill [DIR]` into `.claude/skills/` and `.agents/skills/`. Tested twice with a fresh agent that had only the skill: the first run found an uncapped `review --list` in the skill's own example (it spent $0.0008); fixed, with `HUNCH_MAX_COST=0` as a session-wide net. Not yet: a recipe with its own skill; MCP | [skills from packages](https://docs.getdbt.com/docs/dbt-ai/package-skills.md), [MCP server](https://docs.getdbt.com/docs/dbt-ai/about-mcp.md) | Principle 7. The CLI already is the loop an agent needs; a skill teaches it (lint → compile → `run --max-cost` → test → review → diff, never spend without a cap, read `results.json`). Recipes can carry their own skill | `hunch skill install` (or the file shipped in the package) for Claude Code / Codex / Cursor; one recipe with its own skill. An MCP server only if agents without a shell need it |
| ~~**`hunch docs`**~~ DONE 2026-09-25: one self-contained page per spec or folder (inventory with one status per judgment, including stale when the spec changed after its test; a page per judgment that opens with sentences built from `test`'s numbers; clickable lineage; search) plus `manifest.json`; `description` and `exposures` keys (outside every key and the spec hash); `diff` prints the exposures a change affects; `test` records numbers at the spec's own `act` | [docs commands](https://docs.getdbt.com/reference/commands/cmd-docs.md), [build and view docs](https://docs.getdbt.com/docs/explore/build-and-view-your-docs.md), [exposures](https://docs.getdbt.com/docs/build/exposures.md) | A judgment is a policy that people who do not write code (a PM, a safety or compliance reviewer) have to read and approve; today they would read YAML. dbt's docs matter for data; they matter more for decisions | One static page per project from `manifest.json` + `results.json`: each judgment as a readable policy (questions, options and their meanings, what the model sees, lineage graph), beside what is measured (accuracy with interval, dial, calibration, most confident mistakes, metrics, examples, last run and cost). **Exposures** as metadata: `exposures:` naming the apps, hooks and dashboards that consume a judgment, so docs and `diff` can say what a change affects (`judge(log=True)` can record them) |

**Later, with a trigger.**

| Item | dbt reference | Trigger |
|---|---|---|
| Contracts (options as an interface) | [model contracts](https://docs.getdbt.com/docs/mesh/govern/model-contracts.md), [versions](https://docs.getdbt.com/docs/mesh/govern/model-versions.md) | The first app that branches on a label: renaming an option then breaks it silently. `on_change: freeze` covers part of it today |
| Access, groups, Mesh | [model governance](https://docs.getdbt.com/docs/mesh/govern/about-model-governance.md) | Several teams sharing several projects |
| VS Code extension (inline cost, answers beside the spec, diff on save) | [VS Code extension](https://docs.getdbt.com/docs/about-dbt-extension.md) | Users ask after the schema ships; it is a second codebase to maintain |
| Package hub, `hunch add` | [packages](https://docs.getdbt.com/docs/build/packages.md) | A second recipe author. Until then, recipes from a git URL |
| Stored failures as a table | [store_failures](https://docs.getdbt.com/reference/resource-configs/store_failures.md) | `results.json` lists confident mistakes; a table only if someone queries them |

**Not needed.** [Snapshots](https://docs.getdbt.com/docs/build/snapshots.md): the store keeps every answer under its exact input and `_hunch_row_answers` keeps each row's history across runs. [Source freshness](https://docs.getdbt.com/docs/deploy/source-freshness.md): an orchestration question, and re-running unchanged rows is free. [Jinja](https://docs.getdbt.com/docs/build/jinja-macros.md): a second language, confusion over whether answers are keyed on raw or rendered text, rendering-order bugs; YAML anchors, Python-generated specs (`hunch.load([...])`) and recipes cover the needs (environment variables, if asked, as one small feature). The semantic layer's dimensions, time spines and BI connectors.

**Plugin strategy.** No hunch plugin registry before v1. Each extension point reuses an interface someone else maintains: sources through dlt and `py()`, engines through Pydantic AI's `DecisionModel` (the `pydantic:` adapter above), traces through OpenTelemetry, stores as SQLite now and Postgres later.

## Later, with a trigger: extraction as propose, then verify (discussed 2026-09-25)

**Question.** Should hunch also extract values (names, amounts, dates, claims) with generative models (DeepSeek, any OpenRouter model), so that more of a pipeline can be written as specs and lineage?

**Position.** Not as general generation. hunch is worth using because every answer that code acts on has a measured error rate, and decisions make that cheap: a closed answer set, a probability read from the answer tokens, exact comparison with gold, and the `act` dial. Free-form output breaks each of these: no reliable probability (so no `act`), fuzzy gold matching ("£1,200" against "1200 GBP"), and a `diff` that flags every rewording. A general "any model, any output" layer would also put hunch in a crowded field (DSPy, BAML, Instructor, dbt Python models calling LLMs) where it has no advantage.

**Today, without new code: select instead of generate.** Code lists the candidate values (regex, a parser, spans from a document), and a `choice` question picks one, with `none` when no candidate fits. The answer stays typed, cached, tested and on the dial. This covers more extraction than it first appears to.

**If it earns its place: propose, then verify.**

| Piece | Design |
|---|---|
| `extract` step | A judgment whose question asks an LLM engine for a typed value (`string`, `number`, `date`, or a list of one of these) with structured output. Cached under its exact input like every answer, materialized as a column, available to downstream judgments through `ref()` |
| Accuracy | Exact match against gold after a declared normalisation (`number`, `date`, `casefold`), reported with a Wilson interval like any rate |
| Confidence | Not from the generator. A follow-up `noul` over the source and the proposed value ("Is this value stated in the text?") supplies `p`, so `act`, review, `test`, `metrics` and `examples` work unchanged |
| Lineage | extract → verify → decide composes with what exists: `ref()`, `where`, `chain`, `escalate` |
| Not in scope | Free text (summaries, rewrites), open-ended generation, agents, prompt chains |

**Trigger.** A real use case that selection cannot cover (candidates cannot be listed by code), measured end to end the way the triage, agent-claim and command-guard cookbooks were. Cost when triggered: an engine method for structured output, one question type, normalised gold matching, and a cookbook with honest numbers.

**Measured 2026-09-25: delayed, the confidence is not good enough yet.** The design that fits hunch best is one question type, `extract`, whose engine proposes a value and then reads p(yes) from a yes/no check of it; the value becomes the label and p(yes) the confidence, so gold, reviews, `act`, the dial and calibration would work unchanged. The gate set before building: the check must separate right values from wrong ones (AUROC ≥ 0.8) and be roughly calibrated. A spike on 200 SQuAD v2 dev questions (a hash sample, 107 answerable, 93 not; SQuAD exact match), deepseek-flash proposing at temperature 0, $0.03 in all:

| Check | AUROC | Stated vs actual among p ≥ 0.9 | Right when acting at 0.9 |
|---|---|---|---|
| deepseek-flash checks its own value | 0.71 (0.81 on proposed values, 0.67 on NONE) | 1.00 vs 0.76 | 75.7% of 74% automated |
| Jev (jev-1.13.0) checks deepseek's value, run as a hunch spec | 0.76 | 0.97 vs 0.81 | 80.7% of 60% automated |
| GLiNER2.5-Decide (local encoder, Apache 2.0) checks deepseek's value | 0.45 | 0.96 vs 0.60 | 60.2% of 46% automated |
| GLiNER2.5-base extracts the span itself (question as the field description, best candidate's confidence; 40% exact match) | 0.60 | 0.98 vs 0.46 | 45.5% of 60% automated |
| GLiFormer-base / -large (Knowledgator) extract the span itself (question as the label, best span's score; 42–50% / 43–44% exact match) | 0.51–0.60 / 0.53–0.56 | 0.95 vs 0.46 / 0.96 vs 0.52 | |

Exact match was 66% (83/107 answerable, 49/93 unanswerable). Of the 68 errors, 44 are a plausible value given for a question the text does not answer, 14 an overlapping span that exact match rejects, 6 another wrong value, 4 NONE for an answerable question. Both checks mostly approve the first kind, which is the error that matters in real extraction: a value that looks right and is not in the text. Encoders that only select spans from the text (GLiNER2.5, GLiFormer base and large) cannot write a value the text lacks, but SQuAD v2's unanswerable questions have plausible spans in the text, and GLiNER2.5 picked them confidently; SQuAD's free questions are also not the typed fields (names, amounts, dates) it is trained for, so a field-extraction set would be fairer to it. The sequence probability of the proposed value is no help (DeepSeek at temperature 0 reports every generated token at p = 1). The first spike also showed how easily the check's wording inverts its meaning for NONE (AUROC 0.34 before the fix), a risk every user-written check would carry.

**Reopen when** a check reaches AUROC ≥ 0.8 on this sample (the 200 SQuAD v2 dev questions whose ids have the lowest SHA-256; prompts as described above), for example a newer Jev, a stronger verifier model, or asking the check to quote the supporting span. Until then, select instead of generate.

**Ideas recorded, not scheduled (2026-09-25).**
- *Typed-field test, skipped.* The same candidates (LLM checked by Jev, LLM self-check, GLiNER2.5, GLiFormer) on a public set of typed fields with gold (CUAD contract fields, receipts). Low value for now: typed fields are exactly where code can list candidates, so selection already covers them; a pass would land where hunch already works, a fail would teach nothing new.
- *Cookbook: extract fields by selection.* Candidates from regexes, parsers or an entity model (GLiNER used only to find spans), then a `choice` with `none` picks one, with hunch's confidence, tests and dial. Shows typed extraction with what exists today.
- *A local engine: GLiNER2.5-Decide (Apache 2.0, 0.3–1B, CPU).* A classifier with probabilities whose model card reports it ahead of a Jev model on Fastino's own benchmark. Measure against Jev on BANKING77 (95.8%, same gold and reviews, $0); if competitive, a `gliner:` engine as an optional extra (`hunch-ai[local]`), so the quickstart runs with no API key. As a check of extracted values it was no better than chance (AUROC 0.45 above).

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
- **Spec format**: JSON Schema, versioned; lint built on it. Moved up: see "Next up, from dbt".
- ~~Lineage~~ DONE (04 round 12; per-question `<qid>_key` columns). **Lineage**: `_hunch_run_id` and `_hunch_key` on every materialized row; `_hunch_runs` table (spec hash, git sha, model, cost, status). Downstream reads complete runs only.
- ~~Spec-change policy~~ DONE (04 round 12). **Spec-change policy**: `on_change: reask | new_rows_only | freeze`; `diff` shows the cost before you pay it.
- **Stores**: SQLite (local), Postgres (shared), selected by one string.
- **dbt interop**: read `manifest.json` as sources; emit dbt `sources:` YAML for materialized judgments.
- **Agent skill / MCP**: coding agents write specs and iterate with lint → test → diff. Moved up: see "Next up, from dbt" (skill first, MCP only if needed).
- ~~Recipes as packages~~ first step DONE: `hunch init agent-eval` from the package (no `add`, no hub, no versioning yet). **Recipes as packages**: `hunch init agent-eval`, `hunch add <recipe>`; a recipe hub later. Recipes are versioned, have their own tests and gold, and are overridable (change a threshold or a question without forking).
- **UX pass**: first-ten-minutes path timed with a new user; every error message says what to do next; a Python API (decorators, like dlt) that produces the same spec as the YAML.
- **dlt interop**: dlt resources as sources; hunch results loadable by dlt to any destination.
- **Run-level checks (built in, need run history)**: label-distribution drift vs the previous run, review-rate ceiling, confidence drift, cost budget per run. Generic checks on the output table (nulls, accepted values, ranges) are *not* built: documented as Great Expectations / Soda / dbt tests pointed at hunch's table.

## Ideas borrowed from Great Expectations (UX, not integration)

GX is a reference for what makes quality checks *useful to people*, not a dependency. Decided 2026-09-24: no GX integration unless a user needs it. What to borrow:

- **Named, readable checks.** GX's "expect_column_values_to_be_between" reads like a sentence a non-engineer can review. hunch tests should read the same way in the spec and in reports ("expect accuracy ≥ 90% on the holdout", "expect ≤ 15% sent to review").
- **Docs generated from results.** (Superseded by `hunch docs`, see "Next up, from dbt".) GX's Data Docs turn every validation into a browsable report. hunch equivalent: `hunch report` writes one shareable page per run (dial, calibration, confident mistakes, diff, lineage), like the field-report artifact but generated.
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
