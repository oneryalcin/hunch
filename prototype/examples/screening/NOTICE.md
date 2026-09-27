The reviews come from SYNERGY, De Bruin, J., Ma, Y., Ferdinands, G., Teijema, J. and Van de Schoot, R. (2023),
"SYNERGY - Open machine learning dataset on study selection in systematic reviews",
https://doi.org/10.34894/HE6NAQ, released under CC0 1.0 (https://github.com/asreview/synergy-dataset). Its records
are OpenAlex works (CC0). The reviews used here:

- van Dis et al., "Long-term Outcomes of Cognitive Behavioral Therapy for Anxiety-Related Disorders", JAMA
  Psychiatry, 2020 (https://doi.org/10.1001/jamapsychiatry.2019.3986);
- Moran et al., "Poor nutritional condition promotes high-risk behaviours: a systematic review and
  meta-analysis", Biological Reviews, 2020 (https://doi.org/10.1111/brv.12655);
- Appenzeller-Herzog et al., "Comparative effectiveness of common therapies for Wilson disease: A systematic review
  and meta-analysis of controlled studies", Liver International, 2019 (https://doi.org/10.1111/liv.14179).

Each spec quotes the review's eligibility criteria as SYNERGY records them.

Abstracts can't be republished as plain text, so nothing from them is committed: `fetch.py` rebuilds them from
SYNERGY's inverted index into the gitignored `.cache/`, after `synergy_dataset get` has shown its legal note. Where
OpenAlex has no abstract and the paper has a PubMed id, the abstract comes from PubMed through NCBI's E-utilities
(https://www.ncbi.nlm.nih.gov/books/NBK25497/), cached in `.cache/pubmed/`. The cookbook quotes no abstract.
