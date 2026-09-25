# Writing these docs

Mintlify format (`docs.json` + MDX), hosted on Mintlify from `/docs-site` (Docs7 builds the same files, if we move). Preview: `npx mint dev`.
Checks: `npx mint validate`, `npx mint broken-links`, `uv run docs-site/check.py`.

Goals
- In 60 seconds, a reader can say what hunch is and whether it is for them (index).
- In 5 minutes, they have run, reviewed and tested a judgment (quickstart).
- In 10 minutes, they can explain spec, answer, store, gold, act and review in their own words (concepts).
- Reference pages are complete and dull: every spec key, every command and flag, every variable.

Rules
- Every fact comes from the code or from a command you ran. Paste real output, trimmed; never invent it.
- Examples run as written from the repo root (paths under `prototype/examples/`), with `--max-cost` where they could ask.
- One job per page. Guides do a task and link to reference for details; reference does not teach.
- Plain English, short sentences, no "simply", "just", "blazingly", "powerful", "seamless". One analogy where it pays.
- Say what is measured and how sure it is; say what does not work yet.
- No private data: results from anyone's own sessions are aggregate numbers only, never text from them.
- Components: `<Steps>`, `<Tabs>`, `<Note>`, `<Warning>`, `<Card>`/`<CardGroup>`, mermaid. Nothing custom.
- Frontmatter on every page: `title` and a one-sentence `description`.

Where things live
- `docs-site/`: everything a user reads. The only place user-facing facts are written.
- `docs/`: design notes, research and decisions for people working on hunch. Not published.
