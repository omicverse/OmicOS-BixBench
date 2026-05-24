# Failure case: bix-61-q5 — question asks for Ts/Tv, does not specify variant-quality filtering

**Task**: `bix-61-q5` (BixBench-Verified-50 / WGS — Ts/Tv ratio of an
MDR isolate)
**Harness verdict**: fail
**Agent answer**: `2.56`
**Gold answer**: `2.68`
**Failure class**: under-specified question — gold corresponds to a
filtered VCF; the question does not request filtering.

---

## What the question literally asks

From the per-cell user prompt:

> "What is the Ts/Tv ratio for the MDR sample (SRR35233585) rounded
> to 2 decimal places?"

The question is one sentence. It does not name:

- a quality threshold (`QUAL >= N`),
- a filter status (`PASS` only, `FILTER == "."`, etc.),
- a depth threshold (`DP >= N`),
- a mappability mask,
- bi-allelic vs multi-allelic restriction,
- SNV-only vs include-MNV.

Each of those is a routine VCF preprocessing step in clinical /
microbial genomics practice, but **none is named**. The agent is
asked for "the Ts/Tv ratio for the MDR sample", end of sentence.

## The two defensible readings

1. **Raw VCF, every variant record** (the literal reading). Count
   every transition and every transversion in the unfiltered VCF
   that the workspace ships and report the ratio. **Ts/Tv ≈ 2.56**.

2. **Post-filter VCF** (the implicit best-practice reading). Apply
   the unspecified standard filters first, then count Ts and Tv.
   **Ts/Tv ≈ 2.68**.

The agent went with reading (1). The gold answer is reading (2).

## Why the agent landed on reading (1)

OmicOS surveyed the workspace, located the VCF for SRR35233585,
counted transitions vs transversions across all records, and
reported the ratio rounded to 2 decimal places — every step the
question explicitly names. There is no instruction visible to the
agent that mandates any specific filter, threshold, or set
restriction before the count.

## Why this is filed as a spec issue

VCF best-practice filtering is **a) tool-and-pipeline-dependent**
(`bcftools view -f PASS` vs GATK `VariantFiltration` thresholds vs
sample-specific QC), and **b) absent from the question text**.
Different reasonable defaults give different answers; the gold's
specific filter recipe is not documented in the prompt or any
workspace README the agent can see.

If the question were updated to:

> "What is the Ts/Tv ratio for the MDR sample (SRR35233585)
> **restricted to PASS variants with QUAL ≥ 30**, rounded to 2
> decimal places?"

…then `2.68` would be unambiguous. As written, the agent's reading
of the question — "count Ts and Tv in the VCF" — produces 2.56
without making any modelling assumptions the question forbids.
