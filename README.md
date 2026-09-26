# hunch

**hunch turns a question into a decision your software can act on, and tells you how often it's right.**

Software now makes judgment calls it used to leave to people. Is this command safe to run? Is this alert worth waking someone? Does this contract renew itself? Is the chatbot's answer supported by its source? Which of 50,000 calls mention a competitor? A model answers each in milliseconds for a fraction of a cent. It can't tell you how often it is wrong, or whether yesterday's edit to the question made things worse.

dbt made SQL a practice, analytics engineering. hunch does the same for these decisions: **decision engineering**.

- **Write the question once**, as a short YAML spec in git. The same spec answers a million rows in a batch (`hunch run`) or one row inside your app (`hunch.judge()`), from one cache.
- **Act only when it's sure.** Every answer carries a probability; below the spec's bar, a person decides. `hunch test` shows how many rows a bar automates and how many of those are wrong.
- **Change it without breaking it.** `hunch diff` shows every answer an edit would flip before it ships; tests fail CI when a decision gets worse.
- **Make it better from use.** `hunch review` shows the rows where your verdict teaches the most, and your verdicts become the answer key.
- **Share it.** `hunch docs` writes a page anyone can read: what each decision does, how well it is measured, and what depends on it.

Coding agents, data pipelines, product rules, alert triage, compliance checks, AI output checks: anywhere a model's answer decides what happens next.

## Install

```sh
uv tool install hunch-ai     # or: pip install hunch-ai
```

The package is `hunch-ai`; the command and the import are `hunch`. Python 3.12 or later.

## Try it

```sh
export TYPESAFE_API_KEY=...        # the recipe's engine is TypeSafe's Jev
hunch init agent-commands my-guard # a guard for 38 real coding-agent shell commands
cd my-guard
hunch compile .                    # the exact request and its cost; nothing is sent
hunch run . --max-cost 0.01        # about $0.001
hunch test .
```

Working with a coding agent? `hunch skill` teaches Claude Code, Codex or Cursor the same loop.

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

Engines: TypeSafe's Jev, or any LLM read through its answer-token probabilities (`deepseek:…`, `openrouter:…`). Sources: CSV, coding-agent traces (Claude Code, Cursor, OpenCode, OpenTelemetry), or a Python function.

## Docs

- [Introduction and quickstart](https://fuguai.mintlify.site)
- [Cookbooks](https://fuguai.mintlify.site/cookbooks): real problems with their measured results, including what did not work
- [Spec reference](https://fuguai.mintlify.site/reference/spec)
- [Design notes](docs/README.md), for working on hunch

## Status

v0.2. hunch has run on public datasets, real coding-agent sessions and a 100,000-row test, but no one outside the project has used it yet. Expect the spec format to change before 1.0.

## License

[Apache 2.0](LICENSE), except [`server/`](server/), which is under the [Elastic License 2.0](server/LICENSE). Third-party data used by the examples is listed in [NOTICE](NOTICE).
