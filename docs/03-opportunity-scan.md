# 03 — Opportunity scan: what is "pre-2016 dbt" today?

Question asked: which domain today looks like analytics SQL before dbt and would benefit from a codified approach — LLM-related (Jev, Great Expectations, evals) or not?

Caveat: competitive landscape below is from memory, not researched as of Sep 2026.

## The test

dbt worked because six things were true at once. Score any domain on them:

| dbt ingredient | Question for a domain |
|---|---|
| Narrow declarative primitive (`SELECT`) | Can the unit of work be one small declaration? |
| `ref()` → graph | Are there dependencies between units nobody tracks? |
| Compiles to an existing engine | Is there a cheap engine to target (no runtime to build)? |
| Tests = failing rows | Can "wrong" be expressed as a query? |
| Git / CI / envs | Is logic today in GUIs or strings, untested? |
| Platform shift | Did something just get cheap enough to make this viable? |

**Symptoms of pre-dbt:** nobody can answer "what breaks if I change this?"; changes ship without a before/after diff; logic lives in a GUI, a spreadsheet, or a Python string.

## 1. Judgment layer (dbt × Jev) — CHOSEN → hunch

**Today:** ad-hoc LLM scripts classify tickets, transactions, companies, docs, leads. Prompts in Python strings, no label versioning, a prompt change silently changes thousands of rows, no regression tests, no lineage ("which dashboard uses this label?"), no cost control.

**Primitive:** judgment model = `SELECT`/ref for state + YAML questions (choice/score/noul) with options. Compiles to Jev calls like dbt compiles to warehouse SQL. Output: table of labels + probabilities.

**Tests fall out of Jev's jagged edges:** gold-set accuracy, calibration error < threshold, option-order stability (shuffle, assert no flip — the 0.43→0.63 case), complementary consistency (sum ≈ 1 — the 0.72+0.47 case).

**Patterns dbt lacks:**
- Escalation as a materialization (below-threshold → review queue table).
- Human answers flow back as seeds → gold set → closes the loop.
- Backtest diff in CI: "wording change flips 214/50k rows, here are samples".
- Cost estimate at compile time (tokens × $0.042/M).
- Incremental keyed on hash(input, question).

**LLM product evals are the same system.** Traces are rows; assertions ("did the agent answer the question?", "PII leaked?") are judgments. Jev makes assertions cheap enough for **every production trace**, not a sampled CI set; calibration gives honest warn/error bands.

**Why now:** cheap calibrated judgment engine = the "cloud warehouse" moment.

**Competition:** DSPy (declarative prompt compilation, optimization-focused, not graph/tests/data-ops). Promptfoo / Braintrust / LangSmith (evals = Great Expectations without dbt). Snowflake Cortex / BigQuery AI functions (in-warehouse LLM calls, no discipline). **Gap:** nobody owns graph + tests + backtest + escalation loop for semantic labels.

## 2. Ops automations as code + backtest (not LLM-dependent)

**Today:** Zendesk triggers, Salesforce Flows, HubSpot workflows, routing rules — GUI-clicked. Hundreds of rules interact; nobody knows which fires; no versioning; tested in prod.

**Codified:** rules as declarative files, `ref()` for shared conditions/segments, compile to each vendor's API (Terraform-style).

**Killer feature:** replay last month's events against proposed rules → diff: "this PR re-routes 3.2% of tickets; 40 go to an empty queue". Data-diff for business logic. Jev slots in as a fuzzy condition ("is this a churn threat?").

**Why unsolved:** Terraform covers infra, not business workflows; vendor APIs uneven. Hard but real.

**Relation to hunch:** hunch's backtest diff is the same mechanism. Possible later expansion.

## 3. Financial models as code (not LLM-dependent)

**Today:** Excel = purest pre-dbt. Hidden cell dependencies, copy-paste logic, `v7_FINAL_real.xlsx`, no tests (Reinhart–Rogoff).

**Codified:** assumptions = seeds, calcs = formulas with refs, tests ("balance sheet balances", "cash never negative", "IRR in sane band"), git diff of assumptions, scenarios as environments (base/bull/bear), **compile to a live Excel workbook** (consumers still want Excel).

**Competition:** Pigment, Anaplan, Causal — GUI, not code-first. Finance people don't do git; dbt solved the analog by staying pure SQL → keep Excel formula syntax. Extra "why now": AI-generated models need guardrails.

## 4. Docs tested against reality

**Today:** docs / runbooks / KB rot silently. Docs-as-code solved versioning, not truth.

**Codified:** each page declares what it references (code symbols, endpoints, config keys) → graph; a code change flags affected docs in CI; Jev checks each claim ("given this diff, is this statement still true?"). Smaller wedge, cheap to build, universal pain. **Could be a hunch example project.**

## Weaker candidates

- Alerting / monitor rules — partly covered by Terraform / monitoring-as-code.
- SaaS access control — partly covered by policy-as-code (OPA).
- Legal playbooks — clause → Jev questions, backtest on past contracts; crowded vertical (Ironclad, Spellbook).

## Ranking

| # | Idea | Pain | Why now | Crowding | Jev fit |
|---|---|---|---|---|---|
| 1 | Judgment layer + evals | High, rising | Jev-class engines | Medium, fragmented | Native |
| 2 | Ops automations + backtest | High | APIs mature | Low | Optional |
| 3 | Financial models as code | High | AI-generated models need guardrails | Medium (GUI) | Optional |
| 4 | Tested docs | Medium | Cheap judgments | Low | Native |

**Bet: #1.** Initially framed as "start as a dbt adapter/package to ride distribution"; revised to clean slate with dbt interop — see 04-design.

## Related work / prior art

Checked 2026-09-24.

### jbt — [stelewis/jbt](https://github.com/stelewis/jbt)

"A dbt-like data ingestion pipeline for Plain Text Accounting." Pre-alpha: created 2026-08-19, one PyPI release (`0.1.0a1`, 2026-09-04), 0 stars, Apache 2.0. Code so far is only canonical serialization, SHA-256 content digests, versioned artifact envelopes; no CLI, no pipeline.

| | jbt | hunch |
|---|---|---|
| dbt-like build graph | yes | yes |
| Content-addressed outputs | yes (SHA-256) | yes (core primitive) |
| Versioned human decisions | yes | yes (review write-back) |
| Model-made judgments | **no**, deterministic | **the point** |
| Scope | one domain (accounting) | any rows / traces |

Takeaways:
- **Independent validation** of the content-addressed + versioned-decisions design.
- **Potential user, not competitor**: transaction categorization is a classic judgment problem; jbt's "versioned human decisions" = hunch's review queue output.
- Name `jbt` ruled out (taken, same "dbt-like" framing).

### Roast — [Shopify/roast](https://github.com/Shopify/roast)

Source: "Introducing Roast: Structured AI workflows made easy", Shopify Engineering blog, Obie Fernandez, 2025-06-18. Status 2026-09-24: active, ~1.2k stars, v1.2.0 (2026-06-26), Ruby gem.

**What it is:** convention-over-configuration workflow orchestrator for AI steps. `workflow.yml` + per-step `prompt.md` (ERB templates). Step types: prompt dirs, inline prompts, shell `$(...)`, custom Ruby classes, parallel steps (nested arrays), `each`/`if`/`case` control flow. Built-in tools (ReadFile, Grep, Bash…) and a CodingAgent step (Claude Code). Steps share a conversation transcript. Session replay: every run saved, resume from any step. Built on Raix (provider abstraction, retries, response caching, structured output).

Use cases: grading/fixing unit tests at scale, adding Sorbet types ("Boba"), scanning Slack for early incident signals, competitive-intel reports, "Chesterton's Fence" code-history research.

**Relevance: adjacent prior art, not the same product.**

| | Roast | hunch |
|---|---|---|
| Unit of work | a multi-step *task* (workflow run) | a *judgment over a row*, many rows |
| AI output | generation, agents, free text | selection only: options + calibrated probabilities |
| Declarative YAML, version-controlled | yes | yes |
| Graph | step sequence within a workflow | `ref()` graph across judgments/sources |
| Caching / replay | session replay per run (dev speed) | content-addressed per row (incremental, diff, audit, online) |
| Tests of the AI part | none built-in (testable workflows = code tests) | accuracy, calibration, order stability, consistency |
| Backtest diff on change | no | yes (hero feature) |
| Human review loop | no | yes |
| Analog | GitHub Actions / Airflow for AI tasks | dbt + Great Expectations for AI judgments |

Lessons to steal:
- **Convention over configuration works for AI tooling too** — step = directory + prompt file. Consider: judgment = directory with `judgment.yml` + gold seeds.
- **Replay/caching of expensive AI calls is a "killer feature" for dev loop** — confirms content-addressed store is worth it from day one.
- **"AI as placeholder, replace with deterministic later"** (Sam Schmidt quote). hunch can go further and *measure* it: if a deterministic rule agrees with the judgment on ≥X% of gold rows, suggest demoting the judgment to the rule. Possible feature: `hunch suggest-rules`.
- Their Slack-scanning SRE workflow is a judgment workload in disguise (per-message "is this an early incident signal?") — shows where Roast users would hit hunch's niche.
- Integration, not competition: a Roast step could call `hunch judge` for its classification sub-steps.

### Pydantic AI `TypeSafeModel`, [pydantic.dev/docs/ai/models/typesafe](https://pydantic.dev/docs/ai/models/typesafe/)

Read 2026-09-24. Pydantic AI (the agent framework) runs Jev as a model: each field of an agent's `output_type` becomes one Jev question, the prompt is the text judged, and the filled Pydantic object comes back. Swap the model string and the same agent runs on an LLM. The page also shows Jev as a model router (`SelectModel`), a guard on every tool call, a picker among actions built at run time, a judge of a whole conversation (`judge.run_sync(message_history=...)`), and a `FallbackModel` that hands low-confidence answers to an LLM.

**Relevance: the Python spec format to adopt, a positioning opening, and a risk to watch.**

The type mapping is a de-facto standard Python developers will learn; hunch's Python API should accept it rather than invent another:

| Pydantic type | Jev question | hunch |
|---|---|---|
| `bool` (+ `BoolCriteria` for what yes/no mean) | yes/no | `noul` (criteria true/false supported by the API, not yet by lint/docs) |
| `Literal` / `Enum` with member docstrings | pick one, options described | `choice` |
| `IntEnum` 0..n with a docstring per level | rubric | `score` |
| `float` with `ge=0, le=1` | probability of yes | raw p (`_pyes`) |
| `list[Literal]` / `dict[Literal, bool]` | one yes/no per option | missing: multi-label |
| `Area \| None` | pick one or "none of these" | missing as a primitive (agent_eval hand-built `unclear`) |
| nested model | `outer.inner` questions | not needed yet |

Their docs state hunch's reason to exist: "A classifier in the loop is a component like any other, so it needs the same measurement as the classifier you would deploy on its own … nothing in the run will tell you", and "calibrate each [threshold] against labelled examples of your own". Pydantic AI makes Jev easy to deploy and says it must be measured, but offers nothing to measure it with. Position: **Pydantic AI runs Jev in your agent; hunch tells you whether to trust it.** Integration: a Pydantic output type imports as a hunch spec, so the class that runs live is the class that is tested and diffed.

Risk: Pydantic also has an evals library and an observability product holding traces (from memory, not re-checked). Jev judges + evals + traces in one vendor would cover much of hunch's first use case. Defence is what the page shows no sign of: gold, calibration, the dial, significance-tested diffs, review with gold correction, audit estimates, graphs with chained confidence, a shared content-addressed store.

Details that confirm or change hunch's design:
- **Confirms:** asymmetric thresholds (`typesafe_boolean_threshold`: "the wrong setting for any field where the two mistakes do not cost the same"); option order moves answers, test several orders; pin the version once tuned (`jev-latest` and `jev-preview` move); trim the state, accuracy falls with unrelated context; one judgment per question ("a question that weighs several things at once … returns a plausible number with low confidence").
- **Their confidence is a margin, not a probability:** for yes/no, |p − 0.5| × 2 (p = 0.45 → 0.10; hunch says 0.55). hunch uses probabilities, which is what calibration and chaining need; document the difference so numbers aren't compared across the two.
- **Declining is an option, not an inference:** "declining is something Jev can *choose* rather than something you infer from a low confidence". Same lesson as the review panel's "unclear" traces.
- **Privacy:** state goes to TypeSafe; "send the judge what it needs … rather than the whole argument dict, when those arguments can carry credentials or customer data". Agent traces carry both.
- **Adversarial text moves Jev**, and an agent's own success claim is persuasive text inside agent_eval's `fix_correct` state.
- **Limits:** 64k tokens per request, 32k for state plus the longest question; past it the request fails (`max_tokens_exceeded`). At most 255 options.
- **LLM fallback is another escalation tier**, and "watch how often the fallback fires" is the dial by another name.

### dlt: data load tool, [dlt-hub/dlt](https://github.com/dlt-hub/dlt)

Checked 2026-09-24: ~5.9k stars, Apache 2.0, v1.30.0 (2026-08-11), active since 2022. dlthub.com docs read: README, data-quality lifecycle, schema contracts, state, destination tables & lineage, AI Harness.

**What it is:** the "EL" in ELT as a Python library. Sources (REST APIs described declaratively, SQL databases, files, DataFrames, any generator) → schema inference and normalization → 20+ destinations, swapped by one string. Decorators declare intent: `primary_key`, `write_disposition="merge"`, `dlt.sources.incremental("updated_at")`, `schema_contract`. Business model: open-source `dlt` + commercial **dltHub** (managed runtime, built-in data-quality checks, transformations, an "AI Harness" of skills + MCP + workflow for Claude Code / Cursor / Codex, under a separate license).

**Relevance: complement, not competitor.** dlt's "semantic validity" checks are rules (Pydantic `age > 0`, `is_in()`, filters); nothing judges meaning with a model. The stack reads naturally as **dlt loads → hunch judges → dbt transforms**. Integration points: in-flight via `resource.add_map(...)` / `add_filter(...)` calling `judge()`, or post-load over destination tables.

| | dlt | hunch |
|---|---|---|
| Layer | extract + load | judge (semantic labels) |
| Unit | resource (generator of rows) | judgment (question over rows) |
| Incremental | cursor (`updated_at`) + state | content hash of row text + question |
| Contracts | schema: evolve / freeze / discard_row / discard_value | none yet |
| Lineage | `_dlt_load_id` on every row, `_dlt_loads` (status, schema hash) | answer key only; materialized table has none |
| "Semantic" checks | rules | model judgments with calibration |
| Swap by one string | destination | engine (planned), store (planned) |
| Business model | Apache core + commercial hub | Apache core + ELv2 server (planned) |

**Lessons to take** (details and decisions in 04-design):
1. **Library, not platform.** "Dropped in anywhere: notebook, Lambda, Airflow DAG, laptop, or an AI coding agent." hunch should stay importable with no server needed; the CLI a thin shell over the library.
2. **Lineage columns on every materialized row + a runs table.** dlt's `_dlt_load_id` / `_dlt_loads(status, schema_version_hash)` lets downstream read only complete loads and trace any row. hunch's tables lack this today.
3. **Contracts as explicit policy modes.** dlt names what happens on schema change. hunch needs the same for *spec* change: a reworded question today silently means "re-ask everything".
4. **Cursor + content hash at scale.** Content addressing still reads and hashes every source row each run; dlt's cursor avoids scanning. Large tables want both: cursor to find candidates, hash to decide.
5. **Swap one string.** Engines and stores should switch like dlt destinations.
6. **Agent-native from day one.** dlt ships an LLM-oriented docs index, skills and an MCP server so coding agents build pipelines. hunch specs are ideal agent output (YAML, lint, test, diff give the agent a feedback loop).
7. **The open-core line is validated, and they put data-quality checks in the paid tier.** hunch's tests are its core value and must stay Apache or adoption stalls; paid = hosted review UI, online serving, monitoring (as planned).
8. **Say no to adapters early.** dlt: "New destinations are unlikely to be merged due to high maintenance cost." Keep engine adapters few and pluggable.
9. **Read-back API.** `pipeline.dataset().tracks.df()` / `.arrow()` / `.to_ibis()`. hunch results should be as easy to pull into a DataFrame as they are to query in SQL.
