# BixBench-Verified-50 failure cases

The headline README discusses the **5 questions OmicOS does not pass**
(after the documented per-question grader adjustments — see
[`../grading-deviations.md`](../grading-deviations.md)). One is a
real agent knowledge gap (`bix-16-q1`); the other four sit at the
boundary between **benchmark spec and OmicOS choices**.

This folder collects per-task case studies. They state what the
question literally says, what the gold answer implicitly assumes, and
where OmicOS's answer ended up — without trying to relitigate which
side is "right". The point is to make the spec gap explicit so future
readers can decide for themselves.

## Index — 4 cases at the spec/method boundary

| Task | Spec gap | Agent answer | Gold | File |
|---|---|---:|---:|---|
| `bix-34-q2` | Question asks for "median patristic distance"; the gold uses PhyKIT's `pairwise_distances` median-of-six convention, but the question never names PhyKIT. | (varies by tool) | (PhyKIT-specific) | *coming* |
| `bix-45-q1` | Question says "MWU comparing RCV scores between animal and fungal orthologs" — does not say "shared orthologs". Gold corresponds to the intersection set; the agent computes across all alignments. | `1.5e-56` | `7.7e-54` | [bix-45-q1-question-does-not-specify-shared-orthologs.md](bix-45-q1-question-does-not-specify-shared-orthologs.md) |
| `bix-54-q7` | R `splines::ns(df=4)` knot placement vs Python ports; question does not name R. | (varies by impl) | (R-specific) | *coming* |
| `bix-61-q5` | Question asks for "Ts/Tv ratio for the MDR sample"; does not specify variant-quality filtering. Gold corresponds to a filtered set; the agent reports the raw-VCF value. | `2.56` | `2.68` | [bix-61-q5-question-does-not-specify-variant-filtering.md](bix-61-q5-question-does-not-specify-variant-filtering.md) |

## Not in this folder

- **`bix-16-q1`** — DepMap essentiality sign convention. The agent
  picked the wrong sign; the question is clear; the gold is correct.
  This is a real agent knowledge gap, not a spec issue.
