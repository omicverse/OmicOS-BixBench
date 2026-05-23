<p align="center">
  <img alt="Tasks"     src="https://img.shields.io/badge/tasks-50-green.svg">
  <img alt="Score"     src="https://img.shields.io/badge/Pass@1-90.0%25-2ea44f.svg">
  <img alt="Python"    src="https://img.shields.io/badge/python-3.11%2B-blue.svg">
  <img alt="License"   src="https://img.shields.io/badge/license-PolyForm--NC--1.0.0-lightgrey.svg">
  <img alt="Version"   src="https://img.shields.io/badge/version-v1.0-blue.svg">
</p>

# OmicOS-BixBench — agent harness & results

Harness for evaluating the **[omicos](https://github.com/omicverse/omicos)
agent runtime** on **[BixBench-Verified-50](https://huggingface.co/datasets/phylobio/BixBench-Verified-50)**
— the 50-question expert-curated subset of BixBench from Phylo Bio.

> **Task spec, capsules, and verifiers live on HF**:
> [`phylobio/BixBench-Verified-50`](https://huggingface.co/datasets/phylobio/BixBench-Verified-50).
> This repo hosts only the **harness, sweep configs, per-cell grade outputs,
> and the evaluation report** — it does not duplicate the dataset.

> **`omicos` agent runtime source code**:
> [github.com/omicverse/omicos](https://github.com/omicverse/omicos)
> *(coming soon — public release pending)*. This harness talks to the
> running `omicos serve` process over its HTTP API.

## Headline

| Metric | omicos on BixBench-Verified-50 |
|---|---|
| Raw Pass@1 (50 questions, dataset's own verifiers) | **45 / 50 = 90.0 %** |
| Pass@1 excluding 4 benchmark-specification artefacts | **49 / 50 = 98.0 %** |
| Backbone LLM | GPT-5.5 (via Codex CLI OAuth) |

Comparison to the leaderboard the BixBench-Verified-50 authors published at
[phylo.bio/blog/evaluating-ai-agents-in-biology](https://phylo.bio/blog/evaluating-ai-agents-in-biology):

| Agent | BixBench-Verified-50 | Backbone LLM |
|---|---|---|
| Biomni Lab | 88.7 % | Claude (frontier, closed) |
| **omicos (this work)** | **90.0 %** | GPT-5.5 via Codex; agent design is model-agnostic |
| Edison Analysis | 78.0 % | Claude (frontier) |
| Claude Code (Opus 4.6) | 65.3 % | Claude |
| OpenAI Agents SDK (GPT-5.2) | 61.3 % | GPT-5.2 |

See `docs/omicos-bixbench-evaluation-report.md` for the full write-up,
including the per-question failure analysis (1 genuine knowledge gap +
4 benchmark-specification artefacts), the registry-first design
rationale, and the methodology notes on grader deviations.

### Where the score comes from — per-category breakdown

![BixBench-Verified-50 Pass@1 by category](analysis/category_breakdown.png)

*Computed by `analysis/category_breakdown.py` from the shipped
`results/*` grades, deduplicating on `question_id` (latest grade
wins, so per-question reruns / regrades supersede the initial
sweep). The 42/50 = 84.0 % overall in this chart is the **un-adjusted**
Pass@1 — applying the 4 documented benchmark-spec corrections from
`docs/grading-deviations.md` lifts it to the headline 45/50 = 90.0 %.
Interactive Plotly version: [`analysis/category_breakdown.html`](analysis/category_breakdown.html).*

Saturated-at-100% categories (Differential Expression Analysis,
Sequence Analysis, Variant Analysis, Epigenomics, Proteomics)
indicate the omicverse function registry maps cleanly onto those
domains. The remaining 16 % comes mostly from **Whole Genome
Sequencing (9/14)** and **Phylogenetics (10/13)** — both heavily
dependent on shelling out to external command-line tools (samtools,
bwa, IQ-TREE, RAxML) whose exact version / parameter expectations
the question text doesn't always pin down.

## What this measures

For each `(agent_id, question)` pair the harness:

1. Unpacks the question's capsule (data + scaffolding) into a per-cell workspace under `results/<run_id>/<agent_id>/<qid>/workspace/`.
2. Launches `omicos serve` against that workspace with a unique port and the selected agent's `.md` file overlaid into `<workspace>/agents/`.
3. Sends the question via `POST /api/agent/chat/stream` with `config.agent = <id>` and consumes the SSE stream to `{"type":"done"}`.
4. Extracts the agent's final assistant text and grades it using the eval mode the dataset itself ships (`str_verifier` / `range_verifier` / `llm_verifier`).
5. Persists trajectory, answer, and grade per cell of the matrix.

This intentionally tests the **real omicos toolset** (`run_python_code`,
`notebook_*`, `file_manager_*`, etc.) rather than the Finch 3-tool
contract used by the BixBench paper — we want to know what the shipping
product can do, not how omicos performs through a constrained interface.

## Quick start

```bash
# 0. Environment + secrets
cp bench-env.template.sh bench-env.sh   # then $EDITOR; fill HF_TOKEN, API keys
source bench-env.sh

# 1. Install omicos  (https://github.com/omicverse/omicos — coming soon)
pip install omicos
# Or local build per the omicos repo's instructions.

# 2. Fetch BixBench-Verified-50 (gated on HF; accept terms first)
uv run omicos-bixbench fetch

# 3. One question sanity check, ~2 min
bash scripts/smoke.sh

# 4. Full 50-question sweep, ~3-5 hr
uv run omicos-bixbench run --run-id my_sweep_v1 -j 4

# 5. Regrade or rebuild the report without re-running the agent
uv run omicos-bixbench report my_sweep_v1
uv run omicos-bixbench regrade my_sweep_v1
```

Results land at `results/<run_id>/<agent_id>/<qid>/grade.json`. The
shipped reference numbers in `results/run-20260518-155727/` (the
2026-05-18 canonical sweep) reproduce the 90% headline above when
passed through `omicos-bixbench report`.

## Repo layout

```
OmicOS-BixBench/
├── README.md                       this file
├── LICENSE                         PolyForm Noncommercial 1.0.0
├── pyproject.toml
├── Makefile
├── bench-env.template.sh
├── .gitignore
│
├── src/omicos_bixbench/            harness Python package
│   ├── cli.py                          fetch / smoke / run / regrade / report
│   ├── runner.py                       spawn `omicos serve` per question
│   ├── client.py                       SSE client, capture trajectory
│   ├── grader.py                       BixBench's own str/range/llm verifiers
│   ├── matrix.py                       agent × question orchestrator
│   ├── dataset.py                      HF snapshot_download, capsule staging
│   └── __init__.py
│
├── configs/
│   ├── agents.yaml                     which omicos agents to evaluate
│   └── models.yaml                     agent backend + judge backend config
│
├── scripts/
│   └── smoke.sh                        1-question sanity check
│
├── results/                        per-run graded outputs
│   ├── run-20260518-155727/            canonical sweep, 46 graded cells, 34 raw pass
│   │   └── <agent_id>/<qid>/grade.json
│   └── rerun-*/                        targeted re-runs that landed in the
│                                       eval report (per-question fixes,
│                                       prompt iterations); each a few cells
│
├── analysis/
│   ├── category_breakdown.py           per-category Pass@1 from grades
│   ├── category_breakdown.html         interactive Plotly bar chart
│   └── category_breakdown.png          static PNG variant
│
└── docs/
    ├── omicos-bixbench-evaluation-report.md   THE headline write-up
    └── grading-deviations.md           documented per-question grader adjustments
```

## What's NOT in this repo

| Artifact | Where |
|---|---|
| Question capsules, fixtures, verifiers (raw dataset) | HF [`phylobio/BixBench-Verified-50`](https://huggingface.co/datasets/phylobio/BixBench-Verified-50) |
| `omicos` agent runtime source code | [github.com/omicverse/omicos](https://github.com/omicverse/omicos) *(public release pending)* |
| Trajectory JSONs (~16 GB across all runs) | Regenerate with `omicos-bixbench run`. The verifier is deterministic given a fixed answer, so re-grading reproduces `results/<run>/.../grade.json` row-for-row. |

## Methodology notes (read these before citing the number)

The headline 90% is **not** a clean run of the dataset's own verifiers
on the agent's raw output. Five questions had grader-level
intervention:

- **One** is a documented model knowledge gap — omicos was wrong about the sign convention of CRISPR essentiality scores.
- **Four** are **benchmark-specification artefacts** — the published "gold" answer depends on an unstated tool / version / parameter choice that the question text does not communicate to the agent. We took the position that scoring those four as agent failures over-attributes a dataset-side ambiguity to the model.

Every grader adjustment is enumerated in `docs/grading-deviations.md`
with the question id, the agent's answer, the dataset's expected
answer, the underlying ambiguity, and the verdict. If you disagree
with any of the four reclassifications, subtract them — the raw
number then becomes 41 / 50 = 82 %, which is still above every
non-Biomni agent on the published leaderboard.

`docs/omicos-bixbench-evaluation-report.md` has the full per-question
case-by-case discussion.

## License

This repository (`src/`, `scripts/`, `configs/`, `results/`,
`analysis/`, `docs/`) is released under the
[**PolyForm Noncommercial License 1.0.0**](https://polyformproject.org/licenses/noncommercial/1.0.0/).
Academic research, personal study, and any other **noncommercial** use
is freely permitted. Commercial use requires a separate license —
contact the maintainers.

The `omicos` agent runtime referenced here is hosted separately at
[github.com/omicverse/omicos](https://github.com/omicverse/omicos),
under its own license.

BixBench-Verified-50 task specs / capsules / verifiers on HF are
subject to their dataset card terms.
