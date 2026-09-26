# hunch-server

The hosted layer, licensed under the **Elastic License 2.0** (see `LICENSE`): free to use, modify and self-host; not to be offered to third parties as a hosted or managed service. The engine it serves (`hunch`, `../src`) is Apache 2.0.

It serves the same specs and store as the CLI, so a verdict made here is a review the CLI's `test` and `diff` use, and a row judged here is a cache hit for the next batch.

| | |
|---|---|
| `POST /v1/judge` | `{"path": spec, "row": {...}, "node"?, "shadow"?: candidate, "log"?: true}` → answers (`label`, `p`, `margin`, `route`). A shadow candidate runs after the response, in the server's event loop. |
| `GET /v1/runs?path=` | the project's runs: spec hash, git sha, model, rows, answers asked, cost, status |
| `GET /v1/drift?path=&node=` | per question, the label mix of every run and the change from the previous one (total variation distance; > 0.10 is flagged) |
| `GET /` | projects under the root, their last run |
| `GET /runs?path=` | runs and the latest label mix, drift flagged |
| `GET /review?path=&node=&reviewer=` | the review queue (disputed, audit, uncertain), one click per verdict, written to the judgment's `reviews.csv` |

```sh
cd server
HUNCH_PROJECTS=/path/to/specs HUNCH_SERVER_TOKEN=secret uv run uvicorn hunch_server.app:app --port 8765
curl -H "Authorization: Bearer secret" -X POST localhost:8765/v1/judge \
     -d '{"path": "claude_code/command_guard.yml", "row": {"request": "tidy up", "cwd": "/srv/app", "description": "Remove old logs", "command": "rm -rf /var/log/app/*.gz"}}'
```

Every path is relative to `HUNCH_PROJECTS` and refused if it resolves outside it. With `HUNCH_SERVER_TOKEN` set, every request needs it (header, `?token=`, or the review page's forms); without it the server is open, for local use only.

Not yet: multiple workers sharing one store beyond SQLite WAL, reviewer accounts (the name is a parameter), Postgres.
