# The prompt each reviewer was given

Every round, each of the three reviewers (Claude Opus; Claude Sonnet; Claude Sonnet with the rows in reverse order)
got this prompt with `<packet>` and `<answers>` filled in: `packets/<round_><reviewer>.json` and
`answers/<round_><reviewer>.json`. Nothing else: no conversation, no model answers, no answer key.

Note what it adds to the packet's question: the sentence on model codes that differ "only in formatting" (a trailing
colour or region suffix). The spec's first wording had no such sentence; the final wording has one. So on suffix pairs
the panel had guidance the first wording lacked, and its verdicts there favour the final wording's rule.

---

You are one reviewer on a blind panel labelling product-listing pairs. Read ONLY this file: `<packet>`

It has a "questions" object (one question, `same`, with its meaning and three allowed answers: yes, no, unclear) and a
list of "rows". Each row shows two shops' listings: abt_name, abt_description, abt_price, buy_name, buy_manufacturer,
buy_description, buy_price.

For every row, decide from the text shown alone whether the two listings are the same product, following the
question's wording exactly (same maker and same model; a different model number, size or capacity is a different
product, and so is an accessory made for it; shops word names differently and prices may be missing). Model codes
often differ only in formatting (hyphens, slashes, a trailing colour/region suffix such as LL/A or a colour letter the
other shop omits) — decide whether they denote the same model. Answer "unclear" only when the text really doesn't show
enough.

Rules:
- Do not open any other file, do not search the web, do not run any hunch command, do not spend money. Do not look in
  the key/ or answers/ folders or anywhere else in the repository.
- Judge every row yourself, carefully, one by one. Do not write a script that guesses.
- Write your answers to `<answers>` as a JSON list, one object per row: [{"n": 1, "same": "yes"}, {"n": 2, "same":
  "no"}, ...], covering every n in the packet.
- When done, reply with just the counts of yes / no / unclear.
