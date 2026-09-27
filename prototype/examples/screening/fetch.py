"""Build .cache/<review>.csv: every paper four systematic reviews screened, with the reviewers' decisions.

    uv run --with synergy-dataset python -m synergy_dataset get     # once: downloads SYNERGY and shows its legal note
    uv run python prototype/examples/screening/fetch.py [SOURCE]    # SOURCE: the downloaded synergy-dataset-plus folder

SYNERGY (De Bruin et al. 2023, CC0; see NOTICE.md) stores each paper as an OpenAlex work, with the abstract as an
inverted index because abstracts can't be republished as plain text. This rebuilds the text locally, into the
gitignored .cache/ only. OpenAlex no longer has many abstracts; where a paper has a PubMed id, the abstract comes
from PubMed instead (NCBI E-utilities, cached in .cache/pubmed/). Each row: the paper's title and abstract (empty when
neither source has one), `gold` = the reviewers' title-and-abstract screening decision, `included` = whether the
paper ended up in the review after reading the full text.
"""
import csv
import json
import sys
import time
import urllib.request
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

HERE = Path(__file__).parent
SOURCE = Path.home() / ".synergy_dataset_source" / "synergy-dataset-plus"
REVIEWS = ["van_Dis_2019", "Moran_2020", "Appenzeller-Herzog_2019"]
EFETCH = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi?db=pubmed&retmode=xml&id="


def text(inverted: dict | None) -> str:
    """OpenAlex's abstract_inverted_index ({word: [positions]}) back to plain text."""
    if not inverted:
        return ""
    at = {p: w for w, ps in inverted.items() for p in ps}
    return " ".join(at[p] for p in sorted(at))


def pubmed(pmids: list[str]) -> dict[str, str]:
    """Abstracts by PubMed id, 200 per request, a third of a second apart (NCBI's limit without a key)."""
    cache, out = HERE / ".cache" / "pubmed", {}
    cache.mkdir(exist_ok=True)
    for i in range(0, len(pmids), 200):
        batch = pmids[i:i + 200]
        f = cache / f"{batch[0]}-{len(batch)}.xml"
        if not f.exists():
            f.write_bytes(urllib.request.urlopen(EFETCH + ",".join(batch), timeout=60).read())
            time.sleep(0.34)
        for art in ET.parse(f).getroot().iter("PubmedArticle"):
            pmid = art.findtext(".//PMID")
            parts = ["".join(a.itertext()).strip() for a in art.iter("AbstractText")]
            if pmid and any(parts):
                out[pmid] = " ".join(p for p in parts if p)
    return out


def main(source: Path) -> None:
    (HERE / ".cache").mkdir(exist_ok=True)
    for review in REVIEWS:
        works = {}
        with zipfile.ZipFile(source / review / "works_1.zip") as z:
            for name in z.namelist():
                for w in json.loads(z.read(name)):
                    works[w["id"].lower()] = w
        labels = [(lab, works.get(lab["openalex_id"].lower())) for lab in csv.DictReader(open(source / review / "labels.csv"))]
        labels = [(lab, w) for lab, w in labels if w is not None]
        pmid = {w["id"]: (lab["pmid"] or (w.get("ids") or {}).get("pmid") or "").rsplit("/", 1)[-1] for lab, w in labels}
        own = {w["id"]: text(w.get("abstract_inverted_index") or w.get("abstract_inverted_index_cleaned")) for _, w in labels}
        pm = pubmed(sorted({pmid[i] for i, a in own.items() if not a and pmid[i]}))
        rows = []
        for lab, w in labels:
            rows.append([w["id"].rsplit("/", 1)[-1], (w.get("title") or "").strip(), own[w["id"]] or pm.get(pmid[w["id"]], ""),
                         w.get("publication_year") or "", "yes" if lab["label_abstract_included"] == "1" else "no",
                         "yes" if lab["label_included"] == "1" else "no"])
        with open(HERE / ".cache" / f"{review}.csv", "w", newline="") as f:
            csv.writer(f).writerows([["id", "title", "abstract", "year", "gold", "included"]] + rows)
        no_abstract = sum(not r[2] for r in rows)
        print(f"{review}: {len(rows)} papers, {sum(r[4] == 'yes' for r in rows)} kept at screening, "
              f"{sum(r[5] == 'yes' for r in rows)} included, {no_abstract} without an abstract", file=sys.stderr)


if __name__ == "__main__":
    main(Path(sys.argv[1]) if len(sys.argv) > 1 else SOURCE)
