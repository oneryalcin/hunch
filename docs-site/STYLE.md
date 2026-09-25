# Writing these docs

Mintlify format (`docs.json` + MDX), hosted on Mintlify from `/docs-site` (Docs7 builds the same files, if we move). Preview: `npx mint dev`.
Checks: `npx mint validate`, `npx mint broken-links`, `uv run docs-site/check.py`.

Goals
- In 60 seconds, a reader can say what hunch is and whether it is for them (index).
- In 5 minutes, they have run, reviewed and tested a judgment (quickstart).
- In 10 minutes, they can explain spec, answer, store, gold, act and review in their own words (concepts).
- Reference pages are complete and dull: every spec key, every command and flag, every variable.

Voice
- A calm technical book, not a pitch. Explain an idea; never sell it. No "most teams", no urgency, no adjectives doing the work.
- Build understanding one block at a time. Start from something the reader already knows (a ticket, a test, a dbt model), add one new idea, then the next that rests on it.
- Open with a question the reader can't yet answer, or a real result that surprises. Curiosity is what keeps them reading; answer it before raising the next.
- Each new idea gets one concrete example and at most one analogy, not a list of both.
- Headings say what the section explains ("Every answer is kept"), not what kind of text it is ("The idea in one paragraph", "Overview").
- One idea per paragraph. Short paragraphs, whole sentences, no parenthetical asides stacked inside them.
- Don't dumb it down: use the real term once it has been earned, and link to reference for the rest.
- Cut anything the reader doesn't need for this page's job. Caveats and numbers go where they matter (cookbooks, a `<Note>`), not in the middle of teaching.
- Beware the curse of knowledge: reread each page as someone who has never seen hunch.

Tabs
- Documentation: what hunch is and how to do each task. Guides do a task and link to reference.
- Cookbooks: one real problem each, told as a story with its real numbers: the problem, what we tried, what we found, what didn't work.
- Reference: complete and dull. Every key, command, flag and variable; no teaching.

Rules
- Every fact comes from the code or from a command you ran. Paste real output, trimmed; never invent it.
- Examples run as written from the repo root (paths under `prototype/examples/`), with `--max-cost` where they could ask.
- One job per page.
- Plain English. No "simply", "just", "blazingly", "powerful", "seamless".
- Say what is measured and how sure it is; say what does not work yet.
- No private data: results from anyone's own sessions are aggregate numbers only, never text from them.
- Components: `<Steps>`, `<Tabs>`, `<Note>`, `<Warning>`, `<Card>`/`<CardGroup>`, mermaid. Nothing custom.
- Frontmatter on every page: `title` and a one-sentence `description`.

Where things live
- `docs-site/`: everything a user reads. The only place user-facing facts are written.
- `docs/`: design notes, research and decisions for people working on hunch. Not published.
