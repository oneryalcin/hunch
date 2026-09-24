# prototype

Throwaway single-file prototype of hunch's core loop (see `docs/04-design.md`). Not the real architecture.

```sh
export TYPESAFE_AI_API_KEY=...        # or TYPESAFE_API_KEY
cd example
uv run ../hunch.py compile ticket_triage.yml   # exact request payload + cost estimate
uv run ../hunch.py run     ticket_triage.yml   # ask what's missing, materialize table in .hunch/store.duckdb
uv run ../hunch.py test    ticket_triage.yml   # gold accuracy, list misses, exit 1 on fail
uv run ../hunch.py diff    ticket_triage.yml --against git:HEAD   # or --against old.yml
```

- `example/tickets.csv`: 40 hand-written support tickets with `gold_department`.
- `example/ticket_triage.yml`: one judgment, three questions (choice / noul / score).
- Spec fields `act` and `gold` are hunch-only: never sent to the engine, not part of the cache key.

## What it does

- **Content-addressed cache**: `key = sha256(model, state, api_question)` per (row, question), in DuckDB.
- **Read once**: all uncached questions of a row go in one request.
- **Backtest diff**: old spec (file or `git:REF`) runs on *today's* data; prints flips with before/after, gold accuracy delta, `~noise` flag when either side is within 0.10 of the decision boundary.

## Demo results (2026-09-24, jev-1.13.0)

| Step | Calls | Cost | Result |
|---|---|---|---|
| First `run`, 40 rows × 3 questions | 40 requests / 120 answers, 1.8 s | $0.00066 | 2 rows to review queue |
| Second `run` | 0 | $0 | 0.18 s |
| `test`, bare option labels | 0 | $0 | 97.5%; miss: #21 "annual billing discount?" → billing 0.80 (at threshold, would auto-act) |
| Add option descriptions, `diff` | 40 (department only; others same key) | $0.00069 | 1/40 flips: #21 billing 0.80 → sales 1.00 ✓; accuracy 97.5% → 100% |
| Change only `act` 0.80 → 0.95 | 0 | $0 | review queue 1 row (descriptions made Jev far more confident) |
| Reword `urgent` ("blocked or losing money/users?"), `diff` | 40 | $0.00053 | 10/40 flip; e.g. "Custom contract" → urgent 0.50 (bad), "Cancel and refund" 0.92 → 0.40 (defensible) — visible before shipping |
| Revert wording | 0 | $0 | keys match old answers again |
| `diff --against git:HEAD` in a repo | 0 | $0 | fully served from cache |
