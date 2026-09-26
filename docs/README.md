# Design notes

For people working on hunch: research, design and decisions. Using hunch is covered in the [user docs](https://fuguai.mintlify.site).

| Doc | What |
|---|---|
| [01-jev.md](01-jev.md) | What Jev / System One models are, economics, limits |
| [02-dbt.md](02-dbt.md) | Why dbt won: patterns, extension points, lessons to steal |
| [03-opportunity-scan.md](03-opportunity-scan.md) | Which domains are "pre-2016 dbt"; ranked candidates; why hunch |
| [04-design.md](04-design.md) | Clean-slate rationale, architecture, spec sketch, tests, open questions |
| [05-licensing.md](05-licensing.md) | Split-by-layer license model (Apache 2.0 core + ELv2 server) |
| [06-roadmap.md](06-roadmap.md) | Roadmap, ordered by risk: trust the numbers, the graph, engines and scale, package, server; where we are |
| [07-plugins.md](07-plugins.md) | What plugins solve, who would write them, and what hunch builds (engines only) |

## Decisions so far

| Date | Decision | Why |
|---|---|---|
| 2026-09-24 | Name: `hunch` | System One = gut; a hunch is a calibrated gut call |
| 2026-09-24 | Clean slate, not a dbt package/adapter | Per-row network calls, row-level cache/provenance, human loop, statistical tests, online serving don't fit dbt's model (04-design) |
| 2026-09-24 | dbt interop, not dependency | Read `manifest.json`, write tables back, emit dbt source YAML |
| 2026-09-24 | License split by layer: spec+compiler+CLI Apache 2.0; server ELv2 | Spec adoption needs permissive; protect what a cloud would host (05-licensing) |
| 2026-09-24 | Engine pluggable, Jev first | Avoid single-vendor dependency |
| 2026-09-24 | First user: AI engineer (evals over agent and LLM traces), then analytics engineers, then ops | Strongest evidence so far (SWE-agent: 39% of claimed fixes fail) and the most acute pain (06-roadmap) |
| 2026-09-26 | Category: decision engineering; the value is decisions as code, measurement one property of it | As dbt made analytics engineering a practice; hunch is the default way to put decision models in software, not an eval tool (06-roadmap) |
| 2026-09-26 | Engines are the only code plugin; sources through dlt and `py()`, no sink plugins | A plugin type needs outside authors with a reason to build it (07-plugins) |

## Name

The PyPI name `hunch` is a placeholder someone else holds, so the package is published as `hunch-ai`; the import name and the command stay `hunch` (decided 2026-09-25).
