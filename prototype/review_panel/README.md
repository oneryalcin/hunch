# review_panel

A blind, neutral review of two things we could not check ourselves (2026-09-24):

1. How accurate are Jev and the BANKING77 answer key, really? Our earlier "91%" came from Claude reviewing only disputed rows, which can only move numbers one way.
2. Is Jev's `claims_fixed` column on the SWE-agent traces right? It had no gold, and the overclaiming number depends on it.

Reviewers: three Claude subagents with no conversation context: Opus, Sonnet, and a second Sonnet given the rows in a different order. Majority vote.

## Protocol (why it is neutral)

- **Blind first.** Each BANKING77 row shows only the customer text plus all 77 intents, each defined by 3 examples from the *train* split (the dataset's own definition, not our descriptions). The reviewer picks the best intent and every acceptable intent before looking at candidates.
- **Sources hidden.** Then two candidate labels, in random order, are rated acceptable or not. The reviewer is not told which is the answer key and which is Jev's. On rows where they agree, the second candidate is Jev's runner-up, so agreement is invisible.
- **Unbiased sample.** All 45 holdout rows where Jev ≠ answer key, plus a random 60 of the 340 where they agree (the audit slice).
- **SWE:** 40 random traces, final messages only: "does the agent claim it has fixed the issue? yes / no / unclear".

Files: `packets/` (what reviewers saw), `answers/` (what they wrote), `key/` (mapping back to rows, labels and sources; never shown to reviewers), `score.py` (run `python3 score.py`).

## Results

### BANKING77 holdout (v3 spec)

| | Jev | Answer key |
|---|---|---|
| Estimated accuracy (label judged acceptable) | **95.8%** (95% CI 88.7–97.9%) | 91.9% (84.8–94.0%) |

The 45 disagreements: Jev right and key wrong 20; key right and Jev wrong 5; both acceptable 20; neither 0. Audit slice: the shared label was unacceptable in 2 of 60 agreed rows (3.3%). Reviewer agreement: Fleiss kappa 0.76; unanimous on 29 of 45 disagreements.

"Acceptable" is more lenient than exact-match accuracy: 20 of 45 disagreements had two defensible labels, because BANKING77's intents overlap.

**Check on Claude's own verdicts:** 6 of 13 matched the panel, and in all 7 mismatches Claude had been harsher on Jev than the panel was.

### SWE `claims_fixed`

- Jev matches the reviewer majority on 29 of 30 rows where the reviewers decided (97%).
- Reviewers marked 10 of 40 "unclear"; Jev, forced to answer yes/no, called 7 of them "yes" (5 with p ≥ 0.9).
- Cause: `prepare.py` clipped the *start* of long final messages and dropped the end, where the claim is. Fixed (`clip_tail`); 17 of 200 rows changed. The panel reviewed the pre-fix text.
- On these 40 traces, overclaiming is 26% by Jev's labels and 22% (5/23) by the reviewers'. The full-set figure after the fix is 41% (66/160); the gap to 26% is small-sample noise (8/31 has a 95% CI of roughly 14–43%).

## Caveats

The reviewers are Claude models, the same family as the model that ran the analysis, though with no shared context. This is a neutral panel, not a human one. The audit slice is 60 rows, so the confidence intervals are wide.

## Round 2: the BANKING77 tree's own disputes (Phase 2)

An adversarial review of Phase 2 pointed out that the tree's accuracy estimate reused reviews made for the *flat* model: the tree disagreed with the answer key on 24 rows that no one had reviewed (flat had agreed with the key there), and the estimator extrapolated from rows that were not a random sample. Same protocol, same three reviewers, just those 24 rows (`tree/`, appended by `tree_disputes.py`):

- Answer key right, tree wrong: 19. Both acceptable: 5. Tree right, key wrong: 0. Unanimous on 19 of 24.
- Tree estimate with all 69 of its disagreements reviewed: **87.5% (95% CI 80.0–89.8%)**, not the 89.1% first reported.
- The same 24 reviews then briefly inflated the *flat* estimate (they sit in flat's agreeing rows but were chosen because the tree failed). Reviews now record `kind`: `audit` (random) or `disputed`; only audits stand in for unreviewed agreeing rows. Flat is back to 95.8%.

## Round 3: Claude Code turns (no answer key)

60 random turns from `examples/claude_code` (`claude_code/`: `build.py` → packets, `score.py`, `to_reviews.py`). Each reviewer labels `outcome` (worked / failed / redirected / unclear, from the developer's next message) and `claims_done` (does the final reply say the work is done?), with the spec's own definitions and no model answers.

| | Jev = reviewer majority | reviewers agree |
|---|---|---|
| outcome | 54/60, 90% (95% CI 80–95%) | kappa 0.88, unanimous 53/60 |
| claims_done | 53/60, 88% (78–94%) | kappa 0.86, unanimous 54/60 |

Jev's `outcome` errors: failed → worked ×2, redirected → worked ×2, redirected → unclear, unclear → worked. `claims_done`: yes → no ×5, no → yes ×2. The majority labels are the judgments' reviews (`kind=audit`, a random sample), so `hunch test examples/claude_code` reports the same estimates.
