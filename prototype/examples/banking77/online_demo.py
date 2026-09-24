# /// script
# requires-python = ">=3.12"
# dependencies = ["httpx", "pyyaml"]
# ///
"""Online judge(): same spec, same cache keys as batch. Run after `hunch.py run intent.yml`."""

import csv
import subprocess
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE.parent.parent))
from hunch import judge  # noqa: E402

SPEC = HERE / "intent.yml"


def timed(text: str) -> None:
    t = time.perf_counter()
    r = judge(SPEC, text=text)["intent"]
    ms = (time.perf_counter() - t) * 1000
    print(f"  {ms:7.1f} ms  cached={r['cached']!s:<5}  {r['label']} {r['p']:.2f} → {r['route']:<6}  {text!r}")


print("1. a row the batch already judged: served from the batch's cache")
first = next(csv.DictReader(open(HERE / "banking77_sample.csv")))
timed(first["text"])

print("2. text never seen: one engine call, then cached for everyone")
new = f"my card got stuck in the machine at the train station, ref {int(time.time())}"
timed(new)
timed(new)

print("3. a later batch over a CSV containing that text: already answered, $0")
tmp = Path(tempfile.mkdtemp()) / "online_rows.csv"
with open(tmp, "w", newline="") as f:
    csv.writer(f).writerows([["id", "text"], ["online-1", new]])
out = subprocess.run(["uv", "run", "-q", str(HERE.parent.parent / "hunch.py"), "compile", str(SPEC), "--source", str(tmp)],
                     capture_output=True, text=True).stdout
print("  " + out.strip().splitlines()[-1])
