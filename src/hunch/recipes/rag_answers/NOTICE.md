`answers.csv` holds all 900 answers of the QA test split of RAGTruth (https://github.com/ParticleMedia/RAGTruth), MIT
licence, Copyright (c) 2023 ParticleMedia: answers that six models (GPT-4, GPT-3.5, Llama 2 7B/13B/70B, Mistral 7B)
wrote to MS MARCO questions from the passages they were given, with every unsupported span marked by human
annotators. `gold_unsupported` is `yes` when an annotator marked any span of the answer, `no` otherwise: 160 of the
900. Built from `dataset/response.jsonl` and `dataset/source_info.jsonl` (split `test`, task `QA`).
