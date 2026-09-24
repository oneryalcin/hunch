`traces.csv` is a derived sample (100 resolved + 100 unresolved attempts, one per issue, from parquet shards 0 and 6, seed 2026; built by `prepare.py`) of the SWE-agent-trajectories dataset by Nebius:
https://huggingface.co/datasets/nebius/SWE-agent-trajectories — licensed CC BY 4.0.
Fields are trimmed: issue text (≤4k chars), the agent's last three messages (≤2.5k), the generated patch (≤6k). `resolved` = the dataset's `target` (patch passed the issue's tests).
