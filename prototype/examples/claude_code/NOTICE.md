The example turns come from Trace Commons — Agent Traces (https://huggingface.co/datasets/trace-commons/agent-traces),
licensed CC BY 4.0: real developer ↔ coding-agent sessions from public repositories, anonymized and reviewed by each
contributor. The specs read the 28 Claude Code sessions directly (`source: traces(...)`, split into turns by
`prototype/traces.py`, with long fields clipped and secrets redacted again in the spec). Download them with:

    hf download trace-commons/agent-traces --repo-type dataset --include "sessions/claude_code/*" --local-dir .cache/trace-commons

Nothing from the sessions is committed except `review_panel/claude_code/packets/`: excerpts of 60 turns as the
reviewers saw them. Individual traces may contain material under its own license (see the dataset card).
