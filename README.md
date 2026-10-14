# Calorie-Tracking Fatigue: Online User Discourse Analysis

**Exploratory digital health and human–computer interaction (HCI) research**


## Overview

This project investigates how people describe fatigue, frustration, and disengagement with calorie tracking and food-logging tools, and how online communities respond. The study combines Reddit data collection, balanced sampling, semantic analysis, sentiment analysis, and a structured LLM-assisted qualitative coding workflow.

The research design also describes a completed app-store review phase and a Reddit extension. This repository focuses on the Reddit research pipeline; it does **not** include the full app-store review dataset.

## Research findings

The supplied archived run includes an automated findings report and methods appendix. A carefully qualified summary, including an unresolved difference between reported thread counts, is available in [Research Findings](docs/RESEARCH_FINDINGS.md). These generated results are not independently validated.

## Research questions

- What recurring themes and sentiments appear in calorie-tracking discussions?
- How do users express cognitive burden, emotional strain, and frustration?
- What textual signals suggest disengagement or quitting?
- How do peers respond to people experiencing tracking fatigue?
- What user-centered design implications emerge?

## Workflow

1. **Collect:** script.py uses the Reddit API through PRAW to collect tracking-related posts and comments from selected communities, with a configurable rolling window and per-community/year sample cap.
2. **Baseline analysis:** Pipeline.py uses sentence-transformer embeddings, semantic similarity, and TextBlob sentiment to explore fatigue and support signals.
3. **Structured qualitative analysis:** llm_publication_analysis.py assembles thread packets, performs schema-constrained first-pass coding and second-pass auditing, and creates analysis tables and report materials using the OpenAI API.
4. **Reproducibility:** run_full_study.sh orchestrates the workflow and records outputs and run metadata. run_smoke_test.sh offers a small-scale test.

**Important:** LLM-generated codes and narratives require human review. A second model pass is not independent human inter-rater reliability, and semantic similarity scores are exploratory proxies rather than validated clinical measures.

## Repository files

| File | Purpose |
| --- | --- |
| script.py | Reddit collection and balanced sampling |
| Pipeline.py | Baseline semantic and sentiment analysis |
| llm_publication_analysis.py | Structured two-pass LLM-assisted coding and synthesis |
| run_full_study.sh | End-to-end orchestration |
| run_smoke_test.sh | Small-scale collection/analysis check |
| `.study.env.example` | Example configuration (never commit real API credentials) |
| `STUDENT_PAPER_WRITING_GUIDE.md` | Research paper preparation guidance |
| `docs/Study_Design.pdf` | Research plan |

## Setup

Requires Python 3 and access to Reddit API credentials; the LLM stage additionally requires an OpenAI API key and incurs API charges.

```bash
python -m venv .test-venv
source .test-venv/bin/activate
pip install -r requirements.txt
cp .study.env.example .study.env
```

Set `REDDIT_CLIENT_ID`, `REDDIT_CLIENT_SECRET`, `REDDIT_USER_AGENT`, and `OPENAI_API_KEY` in the local environment file. Review `.study.env.example` for additional settings. The scripts default to `.test-venv/bin/python` unless `PYTHON_BIN` is set.

```bash
bash run_smoke_test.sh
bash run_full_study.sh
```

The full workflow accesses external services and can take substantial time and incur costs. Neither the full run nor publication-level results are independently verified by this README.

## Data and research ethics

Raw Reddit exports may contain usernames, health-related narratives, and quotations that can be traced to individuals. **Do not publish raw datasets, secrets, private run logs, or identifiable evidence excerpts without an appropriate research-ethics and data-sharing review.** Prefer documented schemas and synthetic examples for public reproducibility.

This project studies self-reported discourse, not clinical outcomes or directly observed app retention. Its findings should not be interpreted as causal or representative of all calorie-tracking users.



## Status

Research code and documentation project. Reproducibility depends on API access, environment setup, and independent validation of outputs.

## Rights and access

**All rights reserved.** This public repository is for portfolio review, not a grant of permission to reuse, modify, or redistribute original content. See [LICENSE](LICENSE). Prior licensing grants, if any, may remain effective.

Sensitive raw research data, credentials, and unpublished work should be kept in a separate private repository.
