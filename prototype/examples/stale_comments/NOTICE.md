`comments.csv` is the test split of the just-in-time comment inconsistency dataset of S. Panthaplackel, J. J. Li,
M. Gligoric and R. J. Mooney, "Deep Just-In-Time Inconsistency Detection Between Comments and Source Code", AAAI 2021
(https://github.com/panthap2/deep-jit-inconsistency-detection, MIT licence; data from the Google Drive folder its
README links). Its rows are Java methods and their comments from open-source projects on GitHub, each under its own
project's licence. Built from `Summary/test.json`, `Param/test.json` and `Return/test.json`: `comment` is
`old_comment_raw`, `old_code` and `new_code` are `old_code_raw` and `new_code_raw`, `gold_stale` is `label` (1 → yes),
and `gold_checked` repeats it for the 300 ids in `resources/*/clean_test_ids.json`, the sample the authors checked by
hand. `example/Before.java` and `example/After.java` wrap two of these methods in a class, with Javadoc lines added
around their comments.
