# 02 — What dbt got right (and what to steal)

From memory/general knowledge, not freshly researched.

## What dbt is

SQL-first transformation framework for the T in ELT. Models are `SELECT` statements materialized as views / tables / incremental / ephemeral. Jinja + `ref()`/`source()` build a DAG. Tests (generic `unique`, `not_null`, `accepted_values`, `relationships`; custom singular tests; unit tests since 1.8), seeds, snapshots (SCD2), macros, packages, exposures, semantic layer (MetricFlow). Adapters for Snowflake, BigQuery, Postgres, Databricks, Redshift, DuckDB, etc. dbt Core (OSS) / dbt Cloud (hosted) / Fusion engine (Rust).

## The problem it solved (pre-2016)

Warehouse transforms lived in stored procs, cron jobs, GUI ETL (Informatica, SSIS), scattered unversioned SQL with hand-managed run order. No tests, no docs, no lineage. Cloud warehouses made compute cheap → ELT viable → the missing piece was a way to *manage* in-warehouse transforms.

## The patterns (the secret sauce)

1. **Model = a `SELECT`.** Declare what the data should be; dbt generates DDL/DML. Materialization is a config flag. Separates what from how.
2. **`ref()` → DAG for free.** Dependencies inferred from code. Gives run order, lineage, selective runs (`model+`), parallelism, env swapping (dev/prod schemas). The single biggest idea.
3. **Compile, don't execute.** Thin Jinja layer → plain SQL. No runtime engine, no data movement. Warehouse does the work, so dbt stays tiny.
4. **Software engineering for analysts.** Git, PRs, CI, envs, tests, docs. Tests = queries returning failing rows.
5. **Convention over configuration.** Standard layout, staging → intermediate → marts. Every project looks alike.
6. **Metadata as byproduct.** Docs, lineage, `manifest.json` fall out of writing models; the manifest became a platform.
7. **Incremental + snapshots.** Hard patterns (upserts, SCD2) packaged as config.

## Why it won

- Timing: rode the cloud-warehouse wave and the ETL → ELT shift.
- Persona: SQL-only → analysts took over transformation work; created and evangelized the "analytics engineer" role.
- OSS core, bottom-up adoption, `pip install` and go.
- Community: Slack, Coalesce, package hub, opinionated writing ("dbt viewpoint").
- Scope discipline: only the T. Composes with Fivetran/Airflow instead of competing.
- Legible output: you can always read the compiled SQL. No magic = trust.

**One-liner:** declarative `SELECT` + `ref()` = a DAG; everything else (lineage, tests, docs, CI, envs) falls out of that graph. Simple primitive, massive leverage.

## Extension points

| Kind | Mechanism | Examples |
|---|---|---|
| Packages | `packages.yml` + `dbt deps`, hub.getdbt.com | `dbt-expectations` (Great Expectations-style tests as macros, no GE runtime), `dbt_utils`, `elementary` (observability), `audit_helper`, `codegen`, `dbt_project_evaluator`, Fivetran source packages |
| Adapters | Python plugins per engine | `dbt-snowflake`, `dbt-bigquery`, `dbt-duckdb`, `dbt-clickhouse`, `dbt-trino` … |
| Macro overrides | custom generic tests, custom materializations, `adapter.dispatch`, overriding `generate_schema_name` | |
| Hooks / Python models | `pre-hook`/`post-hook`, `on-run-start/end`; Python models on Snowflake/Databricks/BigQuery | |
| Artifacts ecosystem | `manifest.json`, `run_results.json` | Dagster, Airflow Cosmos, DataHub, Atlan, Monte Carlo, Datafold |

Note: dbt does **not** extract. Loading = Fivetran / Airbyte / dlt / Meltano; dbt only declares `sources:` (with freshness).

## Lessons for hunch

| dbt lesson | hunch translation |
|---|---|
| Narrow primitive (`SELECT`) | Narrow primitive: state + question + options |
| `ref()` → graph | Judgments ref sources, other judgments, dbt models |
| Compile to existing engine | Compile to Jev (or other engine) calls; no model training, no runtime of our own beyond the executor |
| Tests = failing rows | Tests = failing rows **plus** statistical tests (calibration, order stability) |
| Legible compiled output | `hunch compile` shows the exact request payloads |
| Scope discipline | No extraction, no orchestration, no general LLM framework |
| Name a role | Open question: who is the persona? (analytics engineer? "AI engineer"? ops?) |
| Metadata as platform | Emit a manifest; results store is queryable |
