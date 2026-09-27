"""How this process runs: what the command line (or a caller like the server, a hook or hunch.sql) chose.

Read and set them as `settings.X` at call time. `from hunch.settings import MAX_COST` copies the value, so a later
change never reaches it, and `hunch.core.MAX_COST = …` sets nothing hunch reads.
"""
import os
from collections import Counter
from pathlib import Path

SAMPLE: int | None = None  # --sample N: root rows cut to N, fixed by key hash, so repeated samples stay cached
TARGET: str | None = None  # --target NAME: recorded with test results; None runs every spec as written
STORE: Path | None = None  # the store a target names; beats $HUNCH_STORE
VERBOSE = False  # CLI diagnostic detail; measurements and checks never depend on this
# --max-cost (USD per fill): refuse to start if the estimate is above it, and never send a request that could take
# the charged cost above it (see core.worst_cost)
MAX_COST: float | None = float(os.environ["HUNCH_MAX_COST"]) if os.environ.get("HUNCH_MAX_COST") else None
CHARGED = 0.0  # USD charged by every fill in this process, as the engines report it
SPEND_LIMIT: float | None = None  # a total across fills, on CHARGED (hunch.sql's budget: one query, many fills)
RETRIES: Counter = Counter()  # why requests were retried this process (429, 5xx, transport): the scale test reads it
