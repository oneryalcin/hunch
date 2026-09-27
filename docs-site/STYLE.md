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

Cookbook pattern (2026-09-27; the model is `cookbooks/product-matching.mdx`)
- Write it like a good blog post or a Feynman lecture, not a report. A reader should get the whole idea in about three minutes.
- Open with a puzzle from the real data: two or three short lines that make the reader want the answer. Then one plain question ("How would your code tell?").
- `<Info>`: one or two sentences on where this problem shows up and which part hunch is for.
- "Try it first": an interactive widget on real rows, before any explanation (guess, then see the answer and how sure it was).
- Then short numbered steps, each a heading, one or two sentences and the code. Code is the real file, simplified only with a comment saying so, in lines short enough not to scroll sideways.
- Explain every term the first time it appears, with one example; don't use a name the reader hasn't been given (say what `same_code` does, or call it something that says it).
- One animation at most, where the movement is the explanation (a sieve, a sort), started by a button, never autoplay; reduced motion shows the last frame.
- The result as a picture (bars to one scale), plus at most one sentence.
- One surprise or lesson, told as a short story (the fix that broke 40 pairs), ending in the takeaway about hunch.
- "Use it on your data": the command and one sentence.
- Everything rigorous (the data and its licence, intervals, panels, disclosures, what did not work) in `<AccordionGroup>` under "How it was measured". It stays complete and true; it just doesn't interrupt.
- Few numbers in the story, each where it makes a point. No machine-style summaries (a colon, a list of three, packed figures).
- Widget data is generated from cached answers by a script in `docs-site/` (`make_*_data.py`), never hand-edited; widgets live in `snippets/`, use the palette of `snippets/matching.jsx`, and are checked in a browser at desktop and 400px width.

Rules
- Every fact comes from the code or from a command you ran. Paste real output, trimmed; never invent it.
- Examples run as written from the repo root (paths under `prototype/examples/`), with `--max-cost` where they could ask.
- One job per page.
- Plain English. No "simply", "just", "blazingly", "powerful", "seamless".
- Say what is measured and how sure it is; say what does not work yet.
- No private data: results from anyone's own sessions are aggregate numbers only, never text from them.
- Components: `<Steps>`, `<Tabs>`, `<Note>`, `<Info>`, `<Warning>`, `<Card>`/`<CardGroup>`, `<AccordionGroup>`/`<Accordion>` (a cookbook's "How it was measured"), mermaid. Custom components only as interactive teaching aids in `snippets/`: one idea each, earning their place over a static picture (what does dragging show that a table can't?), real data, no autoplay, `prefers-reduced-motion` respected. The prose around a widget must still carry the idea (the `.md` export and agents don't see it). Data files are generated (`make_widget_data.py`), never hand-edited.
- Frontmatter on every page: `title` and a one-sentence `description`.

Where things live
- `docs-site/`: everything a user reads. The only place user-facing facts are written.
- `docs/`: design notes, research and decisions for people working on hunch. Not published.
