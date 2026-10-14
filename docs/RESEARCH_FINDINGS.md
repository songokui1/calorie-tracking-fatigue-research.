# Research findings and methodological context

This page summarizes the archived generated analysis report from the supplied study run (`study_runs/20260324_180958`). The underlying generated report has not been independently validated; these are **automated coding outputs**, not verified publication findings.

## Reported analysis

- Five Reddit communities: `1200isplenty`, `caloriecount`, `loseit`, `myfitnesspal`, and `nutrition`.
- The report describes **803 coded threads**, of which **162 were fatigue-coded** and **651 were help-seeking-coded**. These categories can overlap; do not sum them as mutually exclusive.
- The generated methods appendix separately reports **901 threads coded**, **901 posts loaded**, and **13,121 comments loaded**. **The 803-versus-901 discrepancy must be reconciled before publication or statistical interpretation.**
- The generated report describes fatigue primarily in terms of usability friction, workload, and emotional cost, with constructive peer responses common.
- The temporal breakdown reported for fatigue is 31/149 threads in 2025 and 131/654 in 2026. These two yearly denominators total 803; the sample is uneven by year and is not representative of all Reddit discussion.

## Automated coding workflow

The generated methods appendix describes a thread-level unit of analysis with up to 12 sampled comments per thread, selected using a mixture of highest-scoring and earliest comments. It identifies `gpt-5.4-mini` for initial coding, second-pass audit, and synthesis, with low reasoning effort for coding and audit and medium for synthesis.

A second LLM pass is **not** independent human annotation or a measure of human inter-rater reliability.

## Reproducibility and responsible release

The source scripts in the repository implement data collection, baseline analysis, and LLM-assisted coding. Raw Reddit content, thread-level evidence, and private run artifacts are intentionally excluded from this public release. Researchers should validate output counts against the archived run, review coding quality and quotations manually, and follow applicable ethics and platform policies before publishing results.

This repository is an **exploratory research artifact**, not a peer-reviewed or clinically validated study.
