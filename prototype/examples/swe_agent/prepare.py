# /// script
# requires-python = ">=3.12"
# dependencies = ["httpx", "pyarrow"]
# ///
"""Build traces.csv: a balanced sample of real SWE-agent trajectories with their test outcome.

Source: nebius/SWE-agent-trajectories (CC BY 4.0). `target` = the generated patch passed the
issue's hidden tests. We keep what a reviewer would look at, trimmed to fit Jev's context:
the issue text, the agent's last messages (where it claims success or not), and the patch.

Downloads two ~90 MB parquet shards far apart (rows are grouped by repository) into ./.cache.
"""

import csv
import json
import random
from pathlib import Path

import httpx
import pyarrow.parquet as pq

SHARDS = [0, 6]
URL = "https://huggingface.co/api/datasets/nebius/SWE-agent-trajectories/parquet/default/train/{}.parquet"
PER_CLASS = 100
MAX_ISSUE, MAX_FINAL, MAX_PATCH = 4000, 2500, 6000
CACHE = Path(__file__).parent / ".cache"


def clip(text: str, n: int) -> str:
    return text if len(text) <= n else text[:n] + f"\n…[{len(text) - n} chars cut]"


def clip_tail(text: str, n: int) -> str:
    """Keep the end: an agent's claim of success is in its last words."""
    return text if len(text) <= n else f"[{len(text) - n} chars cut]…\n" + text[-n:]


def issue_of(traj: list[dict]) -> str:
    u = traj[1]["text"]
    start, end = u.find("ISSUE:"), u.find("INSTRUCTIONS:")
    return u[start + len("ISSUE:"): end if end > 0 else None].strip()


def final_of(traj: list[dict]) -> str:
    ai = [m["text"] for m in traj if m["role"] == "ai" and m.get("text")]
    return "\n---\n".join(ai[-3:])


def shard(i: int) -> Path:
    path = CACHE / f"{i}.parquet"
    if not path.exists():
        CACHE.mkdir(exist_ok=True)
        with httpx.stream("GET", URL.format(i), follow_redirects=True, timeout=300) as r:
            r.raise_for_status()
            with open(path, "wb") as f:
                for chunk in r.iter_bytes():
                    f.write(chunk)
    return path


def main() -> None:
    rows = []
    for i in SHARDS:
        t = pq.read_table(shard(i), columns=["instance_id", "model_name", "target", "trajectory", "generated_patch"])
        rows += t.to_pylist()
    rng = random.Random(2026)
    rng.shuffle(rows)
    out, seen, count = [], set(), {True: 0, False: 0}
    for row in rows:
        patch = row["generated_patch"] or ""
        if count[row["target"]] >= PER_CLASS or row["instance_id"] in seen or not patch.strip():
            continue  # one attempt per issue; empty patches are trivially unresolved
        seen.add(row["instance_id"])
        count[row["target"]] += 1
        traj = row["trajectory"] if isinstance(row["trajectory"], list) else json.loads(row["trajectory"])
        out.append({
            "id": f"{row['instance_id']}#{len(out)}",
            "model": row["model_name"],
            "issue": clip(issue_of(traj), MAX_ISSUE),
            "final_messages": clip_tail(final_of(traj), MAX_FINAL),
            "patch": clip(patch, MAX_PATCH),
            "resolved": "yes" if row["target"] else "no",
        })
    with open(Path(__file__).parent / "traces.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(out[0]), lineterminator="\n")
        w.writeheader()
        w.writerows(out)
    print(f"wrote traces.csv: {len(out)} rows, {count}")


if __name__ == "__main__":
    main()
