# Enron email review

Eight lightly redacted real Enron emails, with the TREC 2010 Legal Track's relevance judgments for its lobbying request. The sample is balanced for teaching; its accuracy is not an estimate for the full collection. See `NOTICE.md` for sources and editing details.

Run `hunch compile lobbying.yml`, then `hunch run lobbying.yml --max-cost 0.01` and `hunch test lobbying.yml`.
