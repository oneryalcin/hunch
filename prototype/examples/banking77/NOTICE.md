`banking77_sample.csv` (dev: 10 rows per intent, seed 77) and `banking77_holdout.csv` (holdout: 5 more per intent from the remaining rows, seed 7777) are samples (row `id` = line index in the original test split) of the BANKING77 test set by PolyAI:
https://github.com/PolyAI-LDN/task-specific-datasets — licensed CC BY 4.0.
`banking77_unlabeled.csv` is 3,000 messages (texts only, `id` = t0…t2999, chosen by a hash of the text) from the BANKING77 train split, disjoint from both samples; `distilled/` was trained on Jev's answers to them.
Casanueva et al., "Efficient Intent Detection with Dual Sentence Encoders", NLP4ConvAI 2020.
