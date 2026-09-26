`answers.csv` holds 40 answers from the QA test split of RAGTruth (https://github.com/ParticleMedia/RAGTruth), MIT
licence, Copyright (c) 2023 ParticleMedia: answers that six models (GPT-4, GPT-3.5, Llama 2 7B/13B/70B, Mistral 7B)
wrote to MS MARCO questions from the passages they were given, with every unsupported span marked by human
annotators. `gold_unsupported` is `yes` when an annotator marked any span of the answer, `no` otherwise. 12 of the 40
are `yes`, where the full test split has 160 of 900: this sample is richer in unsupported answers.
