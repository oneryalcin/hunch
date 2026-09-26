# rag-answers

Check a RAG answer against its sources: does it say anything the passages don't support? 40 real answers, one yes/no question; about $0.002 to run.

```
answers.csv (40 rows: question, passages, answer, gold_unsupported)
  └─► answer_support   unsupported: yes / no   (gold: gold_unsupported)
      act on "no" at 0.7: passed as supported; everything else goes to a person
```

```sh
hunch compile .     # what it will ask and cost
hunch run .         # ask, cache, write the answer_support table
hunch test .        # accuracy against gold_unsupported, the two-sided dial, the examples
hunch review .      # rows where the model and the answer key disagree, plus spot checks
```

Replace `answers.csv` with your own question, retrieved passages and answer; in an app, `hunch.judge("answer_support.yml", question=..., passages=..., answer=...)` before an answer is shown.
