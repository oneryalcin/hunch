# Contributing

## The repository

```
src/hunch/            the package (Apache-2.0)
  core.py             specs, lint, sources, engines, the store, run/test/diff/review/suggest, judge()
  traces.py           agent sessions → rows (Claude Code, Cursor, OpenCode, OpenTelemetry); stdlib only
  models.py           Pydantic classes ↔ specs and answers
  cli.py              the `hunch` command: `init` here, everything else in core.main
  recipes/            what `hunch init` copies: tickets, agent_eval
server/               hunch-server, a Starlette app (Elastic License 2.0)
prototype/            examples on public data, review panels, measured results
  hunch.py            runs the package from a checkout, so every documented command works
  examples/           banking77, banking77_tree, swe_agent, claude_code, features, scale, tickets, traces
  review_panel/       blind review protocols and the verdicts behind the published numbers
docs/                 design notes, research and decisions (not published)
docs-site/            the user documentation (published)
```

## Run from a checkout

```sh
uv run prototype/hunch.py test prototype/examples/banking77/intent.yml --max-cost 0
```

The examples' answers are in `prototype/.hunch/store.sqlite` once you have run them. `--max-cost 0` makes any command use cached answers only and refuse to spend, including `suggest`'s writer.

## Check a change

There is no unit-test suite yet. A change is checked by:

1. A reproduction of the bug or behaviour, run before and after the change.
2. A regression comparison: run `test`, `diff`, `compile` and `review --list` on the examples with the old code and the new, on cached answers. A change not meant to alter results must produce identical output.
3. `hunch lint` on every example project.

Add a test only when you can name the production bug it prevents.

## Where decisions live

- `docs/04-design.md`: findings by round: what was measured, what it changed, and every correction.
- `docs/06-roadmap.md`: what comes next and why, ordered by risk.
- `docs/05-licensing.md`: why the split between Apache-2.0 and the Elastic License.

A decision that changes behaviour goes in `docs/04-design.md` with the measurement behind it.

## Write the docs

The site is Mintlify-format MDX in `docs-site/`, hosted on Mintlify, which deploys every push to `main`. Read `docs-site/STYLE.md` first.

```sh
cd docs-site
npx mint dev              # preview at localhost:3000
npx mint validate         # the build
npx mint broken-links     # every internal link and anchor
uv run check.py           # every spec key, CLI flag and environment variable is documented
```

Every fact comes from the code or from a command you ran; paste real output. User-facing facts are written in `docs-site/` only, never in `docs/`.
