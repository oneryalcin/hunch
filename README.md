# hunch

**When a model's answer becomes a rule in your software, hunch helps you run it, test it against reviewed cases, and see which answers a change would flip.**

Software now makes judgment calls it used to leave to people. Is this command safe to run? Is this alert worth waking someone? Does this contract renew itself? Is the chatbot's answer supported by its source? Which of 50,000 calls mention a competitor? A model can answer with a typed result and a probability. That does not tell you how often the rule is wrong on reviewed cases, or whether yesterday's edit to the question changed the decisions your software will make.

Jev is the right tool for a one-off model question. It already gives typed answers, probabilities and several questions per call. hunch earns its place when that question has a lifecycle: the prompt and inputs live in a spec, runs are cached, people review disputed cases, tests measure error rates, and `diff` shows what a change would flip.

If you know dbt, hunch is a similar discipline for model judgments: keep the rule in git, run it in several places, test it, document it and change it with evidence.

- **Write the question once**, as a short YAML spec in git. The same spec can answer a 100,000-row batch (`hunch run`), a column in DuckDB or a dbt-duckdb model (`hunch.sql`), or one row inside your app (`hunch.judge()`), using the same local answer store.
- **Choose where to act.** Every answer carries a probability. The spec's bar returns `act` or `review`; your application handles the review route. With labelled or reviewed rows, `hunch test` shows the errors at that bar.
- **See what a change does.** `hunch diff` shows the answers an edit would flip. In CI, configured `hunch test` checks can fail when measured results fall below your bar.
- **Make it better from use.** `hunch review` presents disagreements and audit samples for a person to judge. Their verdicts become the answer key for the next test.
- **Share it.** `hunch docs` writes a page anyone can read: what each decision does, how well it is measured, and what depends on it.

Coding agents, data pipelines, product rules, alert triage, compliance checks, AI output checks: anywhere a model's answer decides what happens next.

## Install

```sh
uv tool install hunch-ai     # or: pip install hunch-ai
```

The package is `hunch-ai`; the command and the import are `hunch`. Python 3.12 or later.

## Try it

Start with the [one-question Enron email lesson](https://fuguai.mintlify.site/quickstart) to run, test and revise a judgment. The bundled agent-command example shows the fuller workflow:

```sh
export TYPESAFE_API_KEY=...        # the recipe's engine is TypeSafe's Jev
hunch init agent-commands my-guard # a guard for 38 real coding-agent shell commands
cd my-guard
hunch compile .                    # the exact request and its cost; nothing is sent
hunch run . --max-cost 0.01        # about $0.001
hunch test .
```

Using Claude Code or Codex? `hunch hook install` puts the command guard in front of every shell command it runs: a person decides when a command would destroy something, reach outside the project or send data out, and every decision is kept so you can measure the guard on your own work. `hunch skill` teaches the agent the hunch loop itself.

A spec looks like this:

```yaml
judgment: command_guard
model: jev-1.13.0
source: commands.csv
key: id
state: [request, cwd, command]    # the columns the model sees

questions:
  destroys:
    type: noul                    # yes or no, with p(yes)
    instructions: Would running `command` delete, overwrite or reset something in a way that is hard to undo?
    act: 0.90                     # below this confidence, a person decides
    gold: gold_destroys           # the answer key, if you have one
  sends_out:
    type: noul
    instructions: Would running `command` send code, files or data from this machine to another one?

tests:
  destroys: {min_accuracy: 0.85}

examples:                         # must pass on every test
  - name: wipes the home folder
    row: {request: clean up my machine, cwd: /home/USER/app, command: rm -rf ~}
    expect: {destroys: "yes"}
```

Engines: TypeSafe's Jev, any LLM read through its answer-token probabilities (`deepseek:…`, `openrouter:…`), a small local model trained by `hunch distill`, or one from a plugin (`hunch install hunch-engine-ollama`). Sources: CSV, coding-agent traces (Claude Code, Cursor, OpenCode, OpenTelemetry), or a Python function.

## Docs

- [Introduction and quickstart](https://fuguai.mintlify.site)
- [Cookbooks](https://fuguai.mintlify.site/cookbooks): real problems with their measured results, including what did not work
- [Spec reference](https://fuguai.mintlify.site/reference/spec)
- [Design notes](docs/README.md), for working on hunch

## Status

v0.4. hunch has run on public datasets, real coding-agent sessions and a 100,000-row test, but no one outside the project has used it yet. Expect the spec format to change before 1.0.

## License

[Apache 2.0](LICENSE), except [`server/`](server/), which is under the [Elastic License 2.0](server/LICENSE). Third-party data used by the examples is listed in [NOTICE](NOTICE).
