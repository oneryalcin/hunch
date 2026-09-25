# hunch

Your code asks a model small questions all day. Which team should get this ticket? Did the agent's fix work? Is this shell command safe to run? hunch writes each question down as a spec, a short YAML file in git, and tells you how often the answers are right.

- `hunch run` asks the question of every row and stores each answer under its exact input, so a re-run costs nothing.
- `hunch test` measures accuracy against gold, an answer key or your own reviews, as a range, not a single number.
- `hunch diff` shows which rows a change to the question would flip, before you ship it.
- `hunch review` shows you the rows worth reading, and your verdicts become gold.
- `hunch docs` writes a page anyone can read and search: what each judgment decides, how well it was measured, and what depends on it.

If you know dbt, the idea will feel familiar: dbt did this for SQL; hunch does it for model judgments.

## Install

```sh
uv tool install hunch-ai     # or: pip install hunch-ai
```

The package is `hunch-ai`; the command and the import are `hunch`. Python 3.12 or later.

## Try it

```sh
export TYPESAFE_API_KEY=...        # the recipe's engine is TypeSafe's Jev
hunch init tickets my-tickets      # a spec, 40 sample tickets and a README
cd my-tickets
hunch compile .                    # the exact request and its cost; nothing is sent
hunch run . --max-cost 0.01        # about $0.001
hunch test .
```

Working with a coding agent? `hunch skill` teaches Claude Code, Codex or Cursor the same loop.

A spec looks like this:

```yaml
judgment: ticket_triage
model: jev-1.13.0
source: tickets.csv
key: id
state: [subject, body]            # the columns the model sees

questions:
  department:
    type: choice
    instructions: Which team should handle this support ticket?
    criteria:
      billing: Problems with money already charged or owed
      technical: Something is broken, slow, or misbehaving
      sales: Buying more or buying differently
    act: 0.80                     # below this confidence, a person reviews it
    gold: gold_department         # the answer key, if you have one

tests:
  department: {min_accuracy: 0.90}
```

Engines: TypeSafe's Jev, or any LLM read through its answer-token probabilities (`deepseek:…`, `openrouter:…`). Sources: CSV, coding-agent traces (Claude Code, Cursor, OpenCode, OpenTelemetry), or a Python function.

## Docs

- [Introduction and quickstart](https://fuguai.mintlify.site)
- [Cookbooks](https://fuguai.mintlify.site/cookbooks): real problems with their measured results, including what did not work
- [Spec reference](https://fuguai.mintlify.site/reference/spec)
- [Design notes](docs/README.md), for working on hunch

## Status

v0.1. hunch has run on public datasets, real coding-agent sessions and a 100,000-row test, but no one outside the project has used it yet. Expect the spec format to change before 1.0.

## License

[Apache 2.0](LICENSE), except [`server/`](server/), which is under the [Elastic License 2.0](server/LICENSE). Third-party data used by the examples is listed in [NOTICE](NOTICE).
