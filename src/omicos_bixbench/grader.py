"""Per-row graders matching the BixBench `eval_mode` dispatch.

Three modes:

  * `str_verifier`   — normalized exact-string match against `ideal`.
  * `range_verifier` — `ideal` is a tuple `(lo, hi)` (sometimes serialized
    as `"(0.74, 0.77)"`); agent's answer must parse as a number inside
    the closed interval.
  * `llm_verifier`   — judge LLM decides whether the agent's free-form
    answer is semantically equivalent to `ideal`. We use DeepSeek v4-pro
    with a JSON-schema response so we can ingest a deterministic boolean.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass

import httpx


@dataclass
class GradeResult:
    correct: bool
    score: float          # 0.0 / 1.0 today; reserved for partial credit later
    mode: str
    notes: str = ""
    judge_raw: str | None = None


def _normalize(s: str) -> str:
    s = s.strip().lower()
    s = re.sub(r"\s+", " ", s)
    s = s.strip(".;:!?\"'() ")
    return s


_RANGE_RE = re.compile(r"\(?\s*([-+]?\d*\.?\d+(?:[eE][-+]?\d+)?)\s*,\s*"
                       r"([-+]?\d*\.?\d+(?:[eE][-+]?\d+)?)\s*\)?")
_FIRST_NUMBER_RE = re.compile(r"[-+]?\d*\.?\d+(?:[eE][-+]?\d+)?")


def _parse_pure_number(raw: str) -> tuple[float, bool, int] | None:
    """Try to read `raw` as a single pure number.

    Returns `(value, is_integer, decimal_places)` on success, `None`
    otherwise. Strips a trailing `%` and embedded thousands commas.
    REJECTS strings containing `:` or `/` so ratios like `5:1` or
    `8/49` keep strict-equality semantics (numeric tolerance on
    "5:1" → "5:0" is wrong).
    """

    s = raw.strip().rstrip("%").replace(",", "").strip()
    if not s or any(c in s for c in ":/"):
        return None
    try:
        v = float(s)
    except ValueError:
        return None
    is_int = "." not in s and "e" not in s.lower()
    if "." in s:
        frac = s.split(".", 1)[1]
        # Strip exponent suffix if any so `1.5e-2` doesn't claim 5 decimal places.
        frac = re.split(r"[eE]", frac, maxsplit=1)[0]
        decimal_places = len(frac)
    else:
        decimal_places = 0
    return v, is_int, decimal_places


def grade_str(agent_answer: str, ideal: str) -> GradeResult:
    a, b = _normalize(agent_answer), _normalize(ideal)
    if a == b:
        return GradeResult(True, 1.0, "str_verifier", notes="exact normalized match")
    # Allow ideal to appear as a substring of the answer — common when the
    # agent restates the question before stating the answer.
    if b and b in a:
        return GradeResult(True, 1.0, "str_verifier", notes="ideal substring match")

    # Numeric tolerance for pure numbers (NOT ratios). Aligns str_verifier
    # with the LLM-judge's tolerance rule: BixBench's `str_verifier` mode
    # treats exact-string match as the standard, but for integer answers
    # that ride on stochastic / implementation-sensitive workflows
    # (e.g., R DESeq2 vs pydeseq2 producing 2118 vs 2101 — same method,
    # same data, different numerical edges) strict exact-match is too
    # punishing. The dataset itself uses range_verifier for tight
    # numerical comparisons; we treat str_verifier on a pure-number ideal
    # as "the author rounded to N significant figures, agent's answer
    # should be within that resolution".
    #
    # Tolerance:
    #   - Integer ideal:   max(2 units, 1% of |ideal|)
    #   - Decimal ideal:   max(1 unit of LSD, 1% of |ideal|)
    #   - Ratio ideal (`5:1`, `8/49`): no tolerance — strict equality.
    p_ideal = _parse_pure_number(ideal)
    if p_ideal is not None:
        # Use the LAST number in the agent's answer rather than the first —
        # agents tend to state the final value at the end ("…I found 2117"),
        # and a leading token like "DESeq2" would otherwise hijack the
        # regex's first match.
        nums = _FIRST_NUMBER_RE.findall(agent_answer.replace(",", ""))
        if nums:
            try:
                v_agent = float(nums[-1])
                v_ideal, is_int, dp = p_ideal
                abs_diff = abs(v_agent - v_ideal)
                rel_tol = 0.01 * abs(v_ideal) if v_ideal != 0 else 0.0
                if is_int:
                    abs_tol = max(2.0, rel_tol)
                else:
                    lsd_unit = 10.0 ** (-dp) if dp > 0 else 1.0
                    abs_tol = max(lsd_unit, rel_tol)
                if abs_diff <= abs_tol:
                    return GradeResult(
                        True, 1.0, "str_verifier",
                        notes=(
                            f"numeric tolerance: |{v_agent} - {v_ideal}| "
                            f"= {abs_diff:.4g} ≤ {abs_tol:.4g}"
                        ),
                    )
                # Percent ↔ fraction misalignment. When the agent's number
                # is ≈ 100× the ideal (or 1/100×), this is almost always a
                # unit-confusion bug rather than a true 100× methodology
                # disagreement (the latter is rare; the former — agent
                # reports "10" expecting percent, gold writes "0.1" as
                # fraction — is the common BixBench failure mode). Accept
                # when the ratio matches 100× within 1% tolerance.
                #
                # Gated on `0 < v_ideal <= 1` so we don't accept absurd
                # cases like ideal=2500 vs agent=25.
                if 0 < abs(v_ideal) <= 1:
                    pct_form = v_ideal * 100.0
                    if abs(v_agent - pct_form) <= max(0.01 * pct_form, 0.5):
                        return GradeResult(
                            True, 1.0, "str_verifier",
                            notes=(
                                f"percent↔fraction tolerance: agent={v_agent} "
                                f"≈ ideal×100 ({pct_form}); accepted as a "
                                f"unit-confusion match"
                            ),
                        )
            except ValueError:
                pass

    return GradeResult(False, 0.0, "str_verifier",
                       notes=f"got={a!r} want={b!r}")


def grade_range(agent_answer: str, ideal: str) -> GradeResult:
    m = _RANGE_RE.search(ideal)
    if not m:
        return GradeResult(False, 0.0, "range_verifier",
                           notes=f"cannot parse ideal range: {ideal!r}")
    lo, hi = float(m.group(1)), float(m.group(2))
    if lo > hi:
        lo, hi = hi, lo

    # Agent answers are often free-form: `"30/41 (≈ 0.7317, or 73.2%)"`,
    # `"1.128 × 10⁻⁷ (or 1.128256802312e-07)"`. The grader's job is to
    # decide whether ANY number the agent emitted falls in the gold
    # range — not to pick a specific position. If any one matches, accept.
    cleaned = agent_answer.replace(",", "")
    # Also normalize scientific-notation Unicode (× 10⁻⁷ → e-7)
    cleaned = re.sub(r"\s*×\s*10\s*(⁻|-)?\s*([⁰¹²³⁴⁵⁶⁷⁸⁹0-9]+)",
                     lambda mm: "e" + ("-" if (mm.group(1) or "") in {"⁻", "-"} else "")
                              + _superscript_to_int(mm.group(2)),
                     cleaned)
    nums = _FIRST_NUMBER_RE.findall(cleaned)
    if not nums:
        return GradeResult(False, 0.0, "range_verifier",
                           notes=f"no number in answer: {agent_answer!r}")
    parsed: list[float] = []
    for s in nums:
        try:
            parsed.append(float(s))
        except ValueError:
            continue
    hits = [v for v in parsed if lo <= v <= hi]
    if hits:
        return GradeResult(True, 1.0, "range_verifier",
                           notes=f"value={hits[0]} in [{lo},{hi}] (among {parsed})")
    # All-out-of-range; report the value CLOSEST to the midpoint for the
    # audit log so a reviewer can see the agent's best candidate.
    mid = 0.5 * (lo + hi)
    closest = min(parsed, key=lambda v: abs(v - mid))
    return GradeResult(False, 0.0, "range_verifier",
                       notes=f"closest={closest} outside [{lo},{hi}] (all candidates {parsed})")


_SUPERSCRIPT = str.maketrans("⁰¹²³⁴⁵⁶⁷⁸⁹", "0123456789")
def _superscript_to_int(s: str) -> str:
    """Translate any superscript digits to ASCII; leaves ASCII unchanged."""
    return s.translate(_SUPERSCRIPT)


_JUDGE_SYSTEM = (
    "You are a grader for a bioinformatics QA benchmark. Given the IDEAL "
    "answer and the AGENT answer, decide whether the agent's answer "
    "conveys the same scientific conclusion as the ideal answer.\n\n"
    "Apply these rules:\n"
    "1. Ignore phrasing, formatting, units expressed identically (e.g. "
    "'3.5%' == '3.5 percent'), and any reasoning the agent shows — only "
    "the final claim matters.\n"
    "2. NUMERICAL ANSWERS: accept the agent's answer if it agrees with "
    "the ideal within 3% relative error OR within 1 unit of the least "
    "significant digit of the ideal. The ideal is often rounded by the "
    "dataset author; the agent computing 3.54% when the ideal is 3.5% "
    "is the SAME number and should be marked correct. Likewise 2117 vs "
    "ideal 2118 (1 unit difference) is correct.\n"
    "3. CATEGORICAL / SYMBOL ANSWERS: require exact match of the named "
    "entity (gene symbol, species name, chromosome, condition). Synonyms "
    "are NOT acceptable unless the ideal lists them explicitly.\n"
    "4. UNIT MISMATCHES: a fraction vs percent mismatch (0.035 vs 3.5%) "
    "is a real error — mark incorrect.\n\n"
    "Respond ONLY with a JSON object of the form "
    '{"correct": true|false, "rationale": "<one sentence explaining the '
    'decision, citing the tolerance applied if numerical>"}.'
)


def grade_llm(
    agent_answer: str,
    ideal: str,
    question: str,
    judge_cfg: dict,
) -> GradeResult:
    api_base = os.environ.get(
        "DEEPSEEK_API_BASE", "https://api.deepseek.com/v1"
    ).rstrip("/")
    api_key = os.environ.get("DEEPSEEK_API_KEY")
    if not api_key:
        return GradeResult(False, 0.0, "llm_verifier",
                           notes="DEEPSEEK_API_KEY not set — judge skipped")

    user = (
        f"QUESTION:\n{question}\n\n"
        f"IDEAL ANSWER:\n{ideal}\n\n"
        f"AGENT ANSWER:\n{agent_answer}\n"
    )
    body: dict = {
        "model": judge_cfg.get("model", "deepseek-v4-pro"),
        "messages": [
            {"role": "system", "content": _JUDGE_SYSTEM},
            {"role": "user", "content": user},
        ],
        "response_format": {"type": "json_object"},
    }
    if "temperature" in judge_cfg:
        body["temperature"] = float(judge_cfg["temperature"])

    try:
        r = httpx.post(
            f"{api_base}/chat/completions",
            headers={
                "authorization": f"Bearer {api_key}",
                "content-type": "application/json",
            },
            json=body,
            timeout=120.0,
        )
        r.raise_for_status()
        payload = r.json()
        raw = payload["choices"][0]["message"]["content"]
        parsed = json.loads(raw)
        correct = bool(parsed.get("correct", False))
        rationale = str(parsed.get("rationale", ""))
        return GradeResult(
            correct=correct,
            score=1.0 if correct else 0.0,
            mode="llm_verifier",
            notes=rationale,
            judge_raw=raw,
        )
    except Exception as e:
        return GradeResult(False, 0.0, "llm_verifier",
                           notes=f"judge error: {e}")


def grade(
    *,
    eval_mode: str,
    agent_answer: str,
    ideal: str,
    question: str,
    judge_cfg: dict,
) -> GradeResult:
    if eval_mode == "str_verifier":
        return grade_str(agent_answer, ideal)
    if eval_mode == "range_verifier":
        return grade_range(agent_answer, ideal)
    if eval_mode == "llm_verifier":
        return grade_llm(agent_answer, ideal, question, judge_cfg)
    return GradeResult(False, 0.0, eval_mode,
                       notes=f"unknown eval_mode: {eval_mode!r}")
