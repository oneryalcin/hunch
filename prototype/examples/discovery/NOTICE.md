The emails are the EDRM Enron Email Data Set v2, produced by ZL Technologies, Inc. (http://www.zlti.com) and licensed
under the Creative Commons Attribution 3.0 United States License (http://creativecommons.org/licenses/by/3.0/us/), as
the note in every message says. `fetch.py` downloads the text version the TREC Legal Track distributes
(https://trec-legal.umiacs.umd.edu/corpora/trec/legal10/) into the gitignored `.cache/`; nothing from it is committed.
It removes that note from each message and keeps the first 20,000 characters of a message and 10,000 of its
attachments.

The judgments are the TREC 2010 Legal Track Interactive task's relevance judgments for topics 301-304 (NIST,
https://trec.nist.gov/data/legal10.html): `qrel_leg_int_2010_msg_post.txt` (final, after appeals) as gold and
`qrel_leg_int_2010_msg_pre.txt` (the first-pass reviewers' calls). The requests are quoted from the track's Complaint K
(https://trec-legal.umiacs.umd.edu/topics/LT10_Complaint_K_final-corrected.pdf); the senior lawyer's reading in each
spec is our condensation of the Topic-Specific Guidelines
(https://trec.nist.gov/data/legal/10/AssessmentGuidelines_leg_int_2010.pdf). How the sample was drawn and the
teams' results: D. W. Oard et al.'s overview of the track, "Overview of the TREC 2010 Legal Track", TREC 2010
(https://trec.nist.gov/pubs/trec19/papers/LEGAL10.OVERVIEW.pdf).

The messages name real people, most of them Enron employees. They are public records, released in the FERC
investigation; the cookbook quotes no message and names no one.
