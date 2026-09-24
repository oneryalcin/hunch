# /// script
# requires-python = ">=3.12"
# dependencies = ["pyarrow"]
# ///
"""100,000 Amazon reviews (fancyzhx/amazon_polarity test split, Apache 2.0) → .cache/reviews_100k.csv, for the
scale test. Gold `positive` is the dataset's label (yes = 4-5 stars, no = 1-2 stars).

    hf download fancyzhx/amazon_polarity amazon_polarity/test-00000-of-00001.parquet --repo-type dataset --local-dir .cache
    uv run prepare.py
"""
import csv
import random
from pathlib import Path

import pyarrow.parquet as pq

HERE = Path(__file__).parent
t = pq.read_table(HERE / ".cache/amazon_polarity/test-00000-of-00001.parquet").to_pylist()
rows = random.Random(2026).sample(range(len(t)), 100_000)
with open(HERE / ".cache/reviews_100k.csv", "w", newline="") as f:
    w = csv.writer(f, lineterminator="\n")
    w.writerow(["id", "title", "content", "positive"])
    for i in rows:
        w.writerow([i, t[i]["title"], t[i]["content"], "yes" if t[i]["label"] == 1 else "no"])
print(f"{len(rows)} of {len(t)} reviews → .cache/reviews_100k.csv")
