`ragtruth_qa.csv` is the QA test split of RAGTruth (https://github.com/ParticleMedia/RAGTruth), MIT licence,
Copyright (c) 2023 ParticleMedia: 900 answers that six models wrote to MS MARCO questions from the passages they were
given, with every unsupported span marked by human annotators. Built from `dataset/response.jsonl` and
`dataset/source_info.jsonl` (split `test`, task `QA`); `gold_unsupported` is `yes` when any span was marked.
