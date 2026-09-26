`contractnli_test.csv` and `contractnli_dev.csv` are built from the test (123 NDAs) and dev (61 NDAs) splits of
ContractNLI (https://stanfordnlp.github.io/contract-nli/), Hitachi America, Ltd., licensed CC BY 4.0: non-disclosure
agreements collected from the web, each labelled by lawyers against 17 fixed hypotheses. The five `gold_*` columns
are the labels of hypotheses nda-1 (must_be_marked), nda-7 (advisors_allowed), nda-17 (copies_allowed), nda-20
(may_keep_after_return) and nda-19 (survives_termination): Entailment → says_so, Contradiction → says_otherwise,
NotMentioned → not_mentioned.
