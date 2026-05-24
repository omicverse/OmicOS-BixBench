# Failure case: bix-45-q1 — question says "orthologs", does not say "shared orthologs"

**Task**: `bix-45-q1` (BixBench-Verified-50 / phylogenomics — PhyKIT
RCV between animal and fungal orthologs)
**Harness verdict**: fail
**Agent answer**: `1.5197572608715265e-56`
**Gold answer**: `7.6968e-54`
**Failure class**: under-specified question — gold corresponds to a
narrower set than the question's literal wording requires.

---

## What the question literally asks

From the per-cell user prompt:

> "What is the p-value from a Mann-Whitney U test comparing RCV
> scores between **animal and fungal orthologs** using PhyKIT's `rcv`
> function?"

The scope words are **"animal and fungal orthologs"**. There is no
"shared orthologs", "common orthologs", "intersection", or
"orthogroups present in both" in the question text. The system
prompt for this BixBench cell asks the agent to **"match the EXACT
scope words"** — and the exact scope words here do not specify a set
intersection.

## The two defensible readings

1. **All alignments labelled animal or fungal** (the literal reading).
   The agent runs PhyKIT `rcv` on every animal alignment and every
   fungal alignment supplied by the workspace, then a single MWU
   across the two groups. **p ≈ 1.52e-56**.

2. **Only orthogroups represented in both kingdoms** (the
   intersection reading). Take the set of OG IDs that appear on both
   sides, restrict each group to that set, then MWU. **p ≈ 7.70e-54**.

Reading (2) is what the gold answer corresponds to. Reading (1) is
what OmicOS computed.

## Why the agent landed on reading (1)

OmicOS surveyed the workspace, found two flat directories of
alignments tagged by kingdom, applied PhyKIT `rcv` to each alignment
in each kingdom, then ran a two-group MWU. There is no language in
the question that asks the agent to compute the OG-id intersection
first and filter to it.

## Why this is filed as a spec issue

The benchmark cannot mark this as a failure of process: the agent
inspected the data, picked the canonical tool the question names
(PhyKIT `rcv`), ran the canonical test (MWU), and reported the
result in the requested format. The only operation distinguishing
reading (1) from (2) — taking the OG-id intersection — is not
mentioned in the question, the constraints, or any of the workspace
README files visible to the agent.

If the question were updated to:

> "What is the p-value from a Mann-Whitney U test comparing RCV
> scores between animal and fungal orthologs **(restricted to
> orthogroups present in both kingdoms)** using PhyKIT's `rcv`
> function?"

…then `7.7e-54` would be unambiguous and the agent would either
score correct or have a real process gap to fix. As written, both
answers are defensible.
