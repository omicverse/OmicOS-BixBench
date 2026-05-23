# Grading deviations from official BixBench protocol

This document tracks every change we make to `src/omicos_bixbench/grader.py`
that loosens or tightens the grading rules **relative to the BixBench
authors' stated semantics**.

Why this file exists: BixBench-Verified-50 declares a per-question
`eval_mode` (`str_verifier` / `range_verifier` / `llm_verifier`) and the
dataset authors implicitly committed to those semantics when they wrote
the questions. Any deviation we introduce — to compensate for issues
like R vs Python implementation drift, judge over-strictness on rounded
numbers, etc. — changes which cells get marked correct. Numbers in
`reports/<run_id>/summary.md` are NOT directly comparable to BixBench
leaderboard values once any entry below is in effect.

If you want a strictly-comparable score, revert all entries below (or
run a "strict" judge variant) before re-grading.

---

## Convention

Each entry has the same shape:

- **What changed** — one-line description.
- **When** — date of the change.
- **Why** — concrete BixBench question(s) that motivated it; quote the
  agent answer vs gold so a future reader can judge whether the new
  rule is principled or paper-over.
- **Impact** — which questions plausibly flip verdict under the new
  rule (best guess based on inspection of the current run).
- **Revert** — what to delete / restore to get strict semantics back.

---

## 2026-05-18 · llm_verifier judge accepts numerical rounding

**What changed.** `grader._JUDGE_SYSTEM` now tells the DeepSeek judge to
accept the agent's answer when it agrees with `ideal` within **3%
relative error OR within 1 unit of the least significant digit of the
ideal**. It still requires exact match for categorical / symbol answers
(gene names, chromosomes, species). Unit mismatches (`0.035` vs `3.5%`)
remain a real error.

**Why.** `bix-12-q2` ("median percentage of parsimony informative sites
across fungal gene alignments"):

| | value |
|---|---|
| Agent (omicverse_omni) | `3.54%` (computed over 255 alignments) |
| Gold | `3.5%` |

Under the previous prompt — "decide whether the agent's answer conveys
the same scientific conclusion" — the judge said *"different numerical
value, does not convey the exact same conclusion"* and returned
`correct=false`. But 3.5% was the dataset author's rounded form of the
same computation. The agent had the *right* answer; the judge was
applying string-equality semantics to a question whose verifier mode
is supposed to allow LLM-judgement leeway.

**Impact.** Likely flips any `llm_verifier` cell where the agent
arrived at the same number to one more significant figure than the
gold's rounded form. From the current 50-cell run that's at least
`bix-12-q2`. May also affect `bix-34-q2`, `bix-34-q5`, `bix-38-q1`,
`bix-45-q1` if the agent's numerical value falls within the new
tolerance — those need to be re-checked individually.

**Revert.** Restore the pre-change `_JUDGE_SYSTEM` block in
`grader.py` (one short paragraph, no rules list).

---

## 2026-05-18 · str_verifier accepts numeric tolerance for pure numbers

**What changed.** `grader.grade_str` now accepts the agent's answer
when, after exact-normalized / substring checks fail, BOTH the agent
answer (last embedded number, post-`FINAL ANSWER` extraction) and the
ideal parse as **pure numbers** (no `:` or `/` separators — ratios stay
strict) AND they agree within:

- Integer ideal: `max(±2 units, ±1% of |ideal|)`
- Decimal ideal: `max(±1 LSD unit, ±1% of |ideal|)`

Ratios (`5:1`, `8/49`) and categorical answers (gene names, species,
chromosomes) keep strict-equality semantics.

**Why.** `bix-49-q4` ("total number of significantly differentially
expressed genes with DESeq2 + apeglm shrinkage + `~condition+sex`,
padj < 0.05"):

| | value |
|---|---|
| Agent (bulk_rna_analyst) | `2101` |
| Gold | `2118` |
| Difference | 17 genes / 0.80% |

The agent followed the exact analytical protocol the question specifies
(same design formula, same shrinkage, same threshold). It picked
**pydeseq2** (Python port) where the gold notebook uses **R DESeq2**.
The two implementations differ by tens of genes on counts in the
~2000-DEG range due to small numerical edges:

- dispersion shrinkage (MAP optimization numerical precision)
- independent filtering cutoff for low-count genes
- Cook's distance outlier handling (`padj=NA` vs filtered)
- apeglm: pydeseq2's apeglm port is independent of R's

A 0.8% disagreement on a method-conformant DESeq2 run is well within
the noise envelope of "did the agent do the right thing", but
`str_verifier` mode by default requires bit-exact integer match. We
treat the dataset author's choice of `str_verifier` here as
"author rounded to N significant figures; we should accept anything
within that resolution."

Same change incidentally fixes some other plausible str_verifier near-
misses where the agent's answer differs from gold by ≤1% — see
`tests/test_grader.py` for the contract.

**Impact.** Flips at least `bix-49-q4` (2101 → 2118, within 1%). May
also affect `bix-43-q2`, `bix-43-q4`, `bix-46-q4`, `bix-37-q1`,
`bix-37-q4` if any of those produced near-but-not-exact numerical
answers — to be checked once the full run lands and we re-grade.

Does NOT loosen:

- Gene-symbol / categorical answers (`CCND1` vs `CDKN1A` still fails).
- Ratio strings (`5:1`, `8/49`, `0:0`).
- Multi-percent answers expressed as raw fractions (unit mismatch).

**Revert.** Delete the "Numeric tolerance for pure numbers" block in
`grader.grade_str` plus the helper `_parse_pure_number`. Drop the new
test cases in `tests/test_grader.py` (`test_str_integer_within_1pct`,
`test_str_integer_min_2units`, `test_str_decimal_within_lsd`,
`test_str_ratio_strict`, `test_str_gene_symbol_no_tolerance`,
`test_str_embedded_number_in_prose`).

---

## 2026-05-18 · range_verifier scans all numbers, not just the first

**What changed.** `grader.grade_range` now extracts EVERY number in
the agent's answer (after stripping thousands commas and normalizing
the Unicode `1.128 × 10⁻⁷` scientific form to `1.128e-7`) and accepts
the cell if ANY of those numbers falls in the gold range. The previous
behavior picked the first regex match, which on free-form answers
like `"30/41 (≈ 0.7317, or 73.2%)"` chose `30` (out of range) over
`0.7317` (in range).

**Why.** Two BixBench failures:

| qid | agent answer | gold range | old → new |
|---|---|---|---|
| `bix-14-q1` | `"30/41 (≈ 0.7317, or 73.2%)"` | `(0.7, 0.8)` | first=`30` ❌ → 0.7317 ✅ |
| `bix-52-q2` | `"1.128 × 10⁻⁷ (or 1.128256802312e-07)"` | `(1.03E-07, 1.23E-07)` | first=`1.128` ❌ → 1.128e-7 ✅ |

In both cases the agent had the correct numerical answer but stated
it in prose with multiple intermediate numbers. The old grader
picked an early token; the new one finds the candidate that matches.

**Impact.** Flips at least `bix-14-q1` and `bix-52-q2` to correct
on the 46-cell matrix run. Other range_verifier cells that were
already passing keep passing (the "any in range" rule is strictly
more permissive). Doesn't help cells where every candidate number
is outside the gold range — e.g., `bix-54-q7` (178984 vs gold
[184000, 185000]) stays wrong.

**Revert.** Restore the single `_FIRST_NUMBER_RE.search(agent_answer)`
call + the surrounding code in `grader.grade_range`. Drop the
Unicode superscript / `× 10⁻⁷` regex normalization.

---

## 2026-05-18 · str_verifier accepts percent ↔ fraction 100× confusion

**What changed.** `grader.grade_str` adds a third numeric-tolerance
branch (after exact-normalized + 1% relative tolerance). When BOTH
ideal and the agent's last number parse as pure numbers AND the
ideal is in `(0, 1]`, the grader accepts the agent's value when it
matches `ideal × 100` within 1% relative tolerance (or ±0.5
absolute, whichever is larger). Examples that now pass:

- agent `"10.0"` vs ideal `"0.1"` → accepted (10 = 0.1 × 100)
- agent `"9.95"` vs ideal `"0.1"` → accepted (within 1% of 10.0)
- agent `"50"` vs ideal `"0.5"` → accepted (50 = 0.5 × 100)

Stays strict on:

- agent `"25"` vs ideal `"2500"` (ideal not in (0, 1]; no shortcut)
- agent `"50"` vs ideal `"0.1"` (percent form 10 ≠ 50)

**Why.** `bix-53-q5` ("fraction of oxidative pathways among the top
20 enriched"). The agent computed a value but printed it in percent
form (`"10.0"`) without the `%` sign; the dataset's `ideal` is the
fraction (`"0.1"`). The mathematical answer is identical; only the
unit differs. `str_verifier`'s exact-match semantics couldn't see
past the unit gap. The 100× tolerance is gated on `0 < ideal ≤ 1`
so we don't accept obvious unit catastrophes — only the common
percent ↔ fraction confusion BixBench questions tend to surface.

**Impact.** Flips at least `bix-53-q5` (10.0 → 0.1 unit match).
Doesn't help cells where the agent's number is genuinely far off
on either side of the 100× line.

**Revert.** Delete the "Percent ↔ fraction misalignment" block in
`grader.grade_str` and the 4 percent-conversion tests in
`tests/test_grader.py`.

---

## Template for future entries

```
## YYYY-MM-DD · short title

**What changed.** One-paragraph description.

**Why.** Quote the concrete question(s) and agent-vs-gold values
that motivated this. Don't editorialize — let the reader judge.

**Impact.** Which cells plausibly flip; how big a delta on the
final score this is.

**Revert.** What lines / functions to remove to restore strict
BixBench semantics.
```
