"""Build .cache/<topic>.csv: the Enron emails the TREC 2010 Legal Track judged for each topic, with the judgments.

    uv run python prototype/examples/discovery/fetch.py [DIR]      # DIR caches the 625 MB download (default .cache/)

The emails are the EDRM Enron Email Data Set v2 (ZL Technologies, CC BY 3.0 US); the judgments are the track's
final, post-adjudication judgments for topics 301-304 (NIST). See NOTICE.md.

The judged emails are a stratified sample of the 455,449 messages: each row's `stratum` is its probability of being
sampled, and the shares the specs' `weights:` give each stratum are sum(1 / probability) over it, printed here.
"""
import csv
import re
import sys
import tarfile
import urllib.request
from collections import Counter
from pathlib import Path

HERE = Path(__file__).parent
CORPUS = "https://trec-legal.umiacs.umd.edu/corpora/trec/legal10/edrmv2txt-v2.tar.bz2"
QRELS = "https://trec.nist.gov/data/legal/10/qrel_leg_int_2010_msg_post.txt"
FIRST_PASS = "https://trec.nist.gov/data/legal/10/qrel_leg_int_2010_msg_pre.txt"  # the reviewers' calls before appeals
TOPICS = {"301": "drilling", "302": "spills", "303": "lobbying", "304": "privileged"}
KEEP = {"email": 20_000, "attachments": 10_000}  # characters kept: a few emails carry megabytes of attachments
FOOTER = re.compile(r"\*{11}\s*EDRM Enron Email Data Set.*?\*{11}\s*", re.S)  # the licence note in every email


def get(url: str, dest: Path) -> Path:
    if not dest.exists():
        print(f"downloading {url}", file=sys.stderr)
        urllib.request.urlretrieve(url, dest)
    return dest


def main(cache: Path) -> None:
    cache.mkdir(parents=True, exist_ok=True)
    get(FIRST_PASS, cache / "qrels_pre.txt")  # read by measure.py
    judged: dict[str, list[tuple[str, str, str]]] = {}  # message id -> [(topic, yes/no, sampling probability)]
    for line in open(get(QRELS, cache / "qrels.txt")):  # topic, 0, message id, 1 / 0 / -1 (broken), probability
        topic, _, msg, rel, prob = line.split()
        if rel in ("0", "1"):
            judged.setdefault(msg, []).append((topic, "yes" if rel == "1" else "no", prob))
    parts: dict[str, dict[int, str]] = {}
    with tarfile.open(get(CORPUS, cache / "edrmv2txt-v2.tar.bz2"), "r|bz2") as tar:
        for m in tar:
            name = m.name.rsplit("/", 1)[-1].removesuffix(".txt").split(".")  # 3.<n>.<hash>[.<attachment>]
            msg = ".".join(name[:3])
            if m.isfile() and msg in judged:
                text = tar.extractfile(m).read().decode("utf-8", "replace")
                parts.setdefault(msg, {})[int(name[3]) if len(name) > 3 else 0] = FOOTER.sub("", text).strip()
    assert len(parts) == len(judged), f"{len(judged) - len(parts)} judged emails missing from the corpus"
    for topic, name in TOPICS.items():
        rows = []
        for msg in sorted(judged):
            for t, gold, prob in judged[msg]:
                if t == topic:
                    p = parts[msg]
                    atts = "\n\n".join(f"[attachment {k}]\n{p[k]}" for k in sorted(p) if k)
                    rows.append([msg, p.get(0, "")[:KEEP["email"]], atts[:KEEP["attachments"]], gold, prob])
        with open(cache / f"{name}.csv", "w", newline="") as f:
            csv.writer(f).writerows([["id", "email", "attachments", "gold", "stratum"]] + rows)
        n = Counter(r[4] for r in rows)
        pop = {s: c / float(s) for s, c in n.items()}
        share = {s: round(v / sum(pop.values()), 8) for s, v in sorted(pop.items(), key=lambda kv: float(kv[0]))}
        print(f"{name}: {len(rows)} emails, {sum(r[3] == 'yes' for r in rows)} yes, "
              f"{sum(pop.values()):,.0f} messages in the collection; weights population: {share}")


if __name__ == "__main__":
    main(Path(sys.argv[1]) if len(sys.argv) > 1 else HERE / ".cache")
