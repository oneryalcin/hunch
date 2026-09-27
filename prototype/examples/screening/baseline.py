"""The strong baseline: active learning, as ASReview does it. Free and local; about 30 minutes for all three reviews.

    uv run --with asreview python prototype/examples/screening/baseline.py      # after fetch.py

ASReview's simulator plays a reviewer who screens the papers one by one in the order its model suggests, learning
from every decision (the default model, elas_u4), starting from one kept and one rejected paper. It is run three
times per review, from different starting papers, and each reading order is saved as .cache/asreview/<review>_s<seed>.json
for measure.py. The model learns from the screeners' decisions (`gold`); hunch sees none of them.
"""
import csv
import json
import sqlite3
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

HERE = Path(__file__).parent
OUT = HERE / ".cache" / "asreview"
REVIEWS = ["van_Dis_2019", "Moran_2020", "Appenzeller-Herzog_2019"]
SEEDS = [1, 2, 3]


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    csv.field_size_limit(10**8)
    for review in REVIEWS:
        rows = list(csv.DictReader(open(HERE / ".cache" / f"{review}.csv")))
        data = OUT / f"{review}.csv"
        with open(data, "w", newline="") as f:  # ASReview's input: its record_id is the row number here
            w = csv.writer(f)
            w.writerow(["record_id", "title", "abstract", "label_included"])
            w.writerows([i, r["title"], r["abstract"], int(r["gold"] == "yes")] for i, r in enumerate(rows))
        for seed in SEEDS:
            order = OUT / f"{review}_s{seed}.json"
            if order.exists():
                continue
            project = OUT / f"{review}_s{seed}.asreview"
            if not project.exists():
                subprocess.run([sys.executable, "-m", "asreview", "simulate", str(data), "--n-prior-included", "1",
                                "--n-prior-excluded", "1", "--prior-seed", str(seed), "--seed", str(seed),
                                "-o", str(project)], check=True)
            with tempfile.TemporaryDirectory() as d:
                zipfile.ZipFile(project).extract("results.db", d)
                ids = [r[0] for r in sqlite3.connect(Path(d) / "results.db").execute(
                    "select record_id from results order by rowid")]
            order.write_text(json.dumps([rows[i]["id"] for i in ids]))
            print(f"{review} seed {seed}: {len(ids)} of {len(rows)} papers in the order it read them", file=sys.stderr)


if __name__ == "__main__":
    main()
