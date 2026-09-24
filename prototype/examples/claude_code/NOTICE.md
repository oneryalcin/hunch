The example turns come from Trace Commons — Agent Traces (https://huggingface.co/datasets/trace-commons/agent-traces),
licensed CC BY 4.0: real developer ↔ coding-agent sessions from public repositories, anonymized and reviewed by each
contributor. `prepare.py` splits the 28 Claude Code sessions into turns, clips long fields and redacts secrets again;
`turns.csv` is rebuilt locally and not committed. `review_panel/claude_code/packets/` holds excerpts of 60 of those turns.
Individual traces may contain material under its own license (see the dataset card).
