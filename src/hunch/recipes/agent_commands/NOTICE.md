`commands.csv` holds 38 shell commands from the Claude Code sessions in Trace Commons — Agent Traces
(https://huggingface.co/datasets/trace-commons/agent-traces), licensed CC BY 4.0: real developer ↔ coding-agent
sessions from public repositories, anonymized and reviewed by each contributor. Each row is a command with the
request it served, the folder it ran in and the agent's description, as hunch's `commands` trace view reads them,
after redaction (secrets, emails, home folders) and clipping. `gold_destroys` is the verdict of a panel of three AI
reviewers (hunch's review panel, 2026-09-25), not an independent human label.
