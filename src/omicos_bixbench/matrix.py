"""(agent × question) orchestrator — sequential by default.

Each (agent_id, question_id) is one cell. Per cell we:

  1. Stage a fresh per-cell workspace at
     `results/<run_id>/<agent_id>/<qid>/workspace/` (the capsule is unzipped
     here; the held-out notebook is kept on the side for the grader only).
  2. Launch `omicos serve` against that workspace via `runner.serve`.
  3. Send the question text through `client.run_turn` with `config.agent`.
  4. Run the dataset's own verifier on the final answer.
  5. Persist `answer.json`, `grade.json`, `sse.log` next to the workspace.

Parallelism: omicos enforces a per-workspace lock, but since we stamp out
unique workspaces per cell, multiple cells could in principle run on
disjoint ports concurrently. Today we keep this sequential because each
omicos process loads its own Python kernel (~GB of RSS); concurrency
multiplies that, and the LLM bottleneck is provider-side anyway. The
`concurrency` parameter is a future hook.
"""

from __future__ import annotations

import json
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterable

import yaml

from . import client as ob_client
from . import dataset as ob_dataset
from . import grader as ob_grader
from . import runner as ob_runner


def _user_prompt(q: ob_dataset.Question) -> str:
    """Construct the prompt we hand to the agent. Plain English instruction
    + final-answer marker so `client._extract_final_answer` can recover the
    grader's input deterministically."""

    return (
        "You are answering ONE question from a published bioinformatics "
        "benchmark. The data files for this task are already present in "
        "your current working directory; use your tools to inspect them, "
        "run any code or notebooks you need, and arrive at the answer.\n\n"
        f"QUESTION: {q.question}\n\n"
        "Constraints:\n"
        "- All evidence must come from the files in this workspace; do not "
        "guess from prior knowledge alone.\n"
        "- This is a non-interactive benchmark run. Do NOT use plan mode "
        "(`plan__enter` / `plan__write` / `plan__request_approval`) — "
        "there is no human reviewer to approve a plan. Execute the work "
        "directly with `run_python_code` / `file_manager__*` / `skill` / "
        "`shell` and report the result.\n"
        "- Read the question literally and match the EXACT scope words it "
        "uses (e.g. 'across all genes' vs 'shared orthogroups', 'top 20' "
        "vs 'all enriched', 'fraction' vs 'count', 'rounded to 2 decimals' "
        "vs raw). If you computed multiple variants for sanity checks, "
        "report only the one whose scope matches the question's wording; "
        "the others are diagnostic, not the answer.\n"
        "- Show your work briefly, then end your final assistant message "
        "with a single line of the form:\n\n"
        "  FINAL ANSWER: <your answer>\n"
    )


@dataclass
class CellResult:
    run_id: str
    agent_id: str
    question_id: str
    eval_mode: str
    categories: list[str]
    correct: bool
    score: float
    final_answer: str
    final_text: str
    grader_notes: str
    error: str | None
    elapsed_s: float
    tool_calls: int
    input_tokens: int
    output_tokens: int


def _load_yaml(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def _agent_md_exists(agent_id: str, agents_dir: Path) -> bool:
    return (agents_dir / f"{agent_id}.md").is_file()


def _select_agent(
    q: ob_dataset.Question,
    agents_cfg: list[dict],
    agents_overlay: Path,
) -> dict | None:
    """First-match-wins agent selection for one question.

    Walks `agents_cfg` in declaration order; returns the first agent that
    claims the question via `question_ids` (explicit allowlist), or via
    `categories` (substring fallback), or has neither filter (catch-all).
    The catch-all should be declared LAST so specialists win.

    Skips agents whose admin .md isn't locally available — that way a
    tier-incomplete environment still produces a clean report rather than
    failing the cell.
    """

    for agent in agents_cfg:
        agent_id = agent["id"]
        if agents_overlay.is_dir() and not _agent_md_exists(agent_id, agents_overlay):
            continue
        ids = agent.get("question_ids") or []
        if ids:
            if q.question_id in set(ids):
                return agent
            continue
        cats = agent.get("categories") or []
        if cats:
            want = [c.lower() for c in cats]
            have = [c.lower() for c in q.categories]
            if any(w in h for w in want for h in have):
                return agent
            continue
        # No filter on this agent — catch-all.
        return agent
    return None


# Stdout lock so concurrent cells don't interleave their progress prints.
_log_lock = threading.Lock()


def _emit(msg: str) -> None:
    with _log_lock:
        print(msg, flush=True)


def run_matrix(
    *,
    project_root: Path,
    run_id: str,
    agents_yaml: Path,
    models_yaml: Path,
    questions: Iterable[ob_dataset.Question],
    concurrency: int = 1,
) -> list[CellResult]:
    """First-match-wins assignment + optional parallel execution.

    `concurrency` controls how many cells run in parallel. Each cell
    spawns its own `omicos serve` on a free port against its own
    workspace, so the only shared resource is host RAM (each cell
    eventually loads a Python kernel — budget ~1-3 GB per concurrent
    cell once the kernel + capsule data are in memory).
    """

    agents_cfg = _load_yaml(agents_yaml).get("agents", [])
    models_cfg = _load_yaml(models_yaml)
    agent_model = models_cfg.get("agent_model", {})
    judge_model = models_cfg.get("judge_model", {})

    agents_overlay = Path(
        os.environ.get(
            "OMICOS_BIXBENCH_AGENTS_DIR",
            os.path.expanduser("~/omicverse/omicos-admin/agents"),
        )
    )

    run_root = project_root / "results" / run_id
    run_root.mkdir(parents=True, exist_ok=True)

    # Resolve every (agent, question) pair UP FRONT so we know the total
    # cell count before spawning threads (and we surface unassigned
    # questions clearly instead of silently dropping them).
    questions_list = list(questions)
    assignments: list[tuple[dict, ob_dataset.Question]] = []
    unassigned: list[str] = []
    for q in questions_list:
        agent = _select_agent(q, agents_cfg, agents_overlay)
        if agent is None:
            unassigned.append(q.question_id)
            continue
        assignments.append((agent, q))

    if unassigned:
        _emit(
            f"[matrix] WARNING: {len(unassigned)} question(s) had no matching agent "
            f"and were skipped: {unassigned}"
        )

    _emit(
        f"[matrix] {len(assignments)} cell(s) to run "
        f"({len(questions_list)} questions, concurrency={concurrency})"
    )

    results: list[CellResult] = [None] * len(assignments)  # type: ignore[list-item]
    if concurrency <= 1:
        for i, (agent, q) in enumerate(assignments):
            results[i] = _run_cell(
                run_id=run_id,
                run_root=run_root,
                agent_id=agent["id"],
                question=q,
                agent_model=agent_model,
                judge_model=judge_model,
            )
            _emit(
                f"[matrix] {i+1}/{len(assignments)} done: "
                f"{agent['id']}/{q.question_id} "
                f"correct={results[i].correct} "
                f"elapsed={results[i].elapsed_s:.1f}s"
            )
        return results

    # Parallel path. Each cell is fully isolated (own workspace + port).
    done_count = 0
    with ThreadPoolExecutor(max_workers=concurrency) as pool:
        future_to_idx = {}
        for i, (agent, q) in enumerate(assignments):
            fut = pool.submit(
                _run_cell,
                run_id=run_id,
                run_root=run_root,
                agent_id=agent["id"],
                question=q,
                agent_model=agent_model,
                judge_model=judge_model,
            )
            future_to_idx[fut] = (i, agent["id"], q.question_id)
        for fut in as_completed(future_to_idx):
            i, aid, qid = future_to_idx[fut]
            try:
                results[i] = fut.result()
            except Exception as e:  # pragma: no cover — defensive
                _emit(f"[matrix] ERROR {aid}/{qid}: {e!r}")
                # Leave None in the slot; filtered out below.
                continue
            done_count += 1
            _emit(
                f"[matrix] {done_count}/{len(assignments)} done: "
                f"{aid}/{qid} correct={results[i].correct} "
                f"elapsed={results[i].elapsed_s:.1f}s"
            )
    return [r for r in results if r is not None]


def _run_cell(
    *,
    run_id: str,
    run_root: Path,
    agent_id: str,
    question: ob_dataset.Question,
    agent_model: dict,
    judge_model: dict,
) -> CellResult:
    cell_dir = run_root / agent_id / question.question_id
    cell_dir.mkdir(parents=True, exist_ok=True)
    workspace = ob_dataset.stage_question(question, cell_dir)
    sse_log = cell_dir / "sse.log"
    started = time.monotonic()
    error: str | None = None
    turn: ob_client.TurnResult | None = None

    try:
        with ob_runner.serve(workspace, log_path=cell_dir / "omicos.log") as proc:
            turn = ob_client.run_turn(
                base_url=proc.base_url,
                agent_id=agent_id,
                user_message=_user_prompt(question),
                model_cfg=agent_model,
                sse_log=sse_log,
            )
    except Exception as e:
        error = f"{type(e).__name__}: {e}"

    if turn is None:
        cell = CellResult(
            run_id=run_id,
            agent_id=agent_id,
            question_id=question.question_id,
            eval_mode=question.eval_mode,
            categories=question.categories,
            correct=False,
            score=0.0,
            final_answer="",
            final_text="",
            grader_notes="serve/turn failed",
            error=error or "unknown",
            elapsed_s=time.monotonic() - started,
            tool_calls=0,
            input_tokens=0,
            output_tokens=0,
        )
    else:
        grade = ob_grader.grade(
            eval_mode=question.eval_mode,
            agent_answer=turn.final_answer,
            ideal=question.ideal,
            question=question.question,
            judge_cfg=judge_model,
        )
        cell = CellResult(
            run_id=run_id,
            agent_id=agent_id,
            question_id=question.question_id,
            eval_mode=question.eval_mode,
            categories=question.categories,
            correct=grade.correct,
            score=grade.score,
            final_answer=turn.final_answer,
            final_text=turn.final_text,
            grader_notes=grade.notes,
            error=turn.error,
            elapsed_s=turn.elapsed_s,
            tool_calls=turn.tool_calls,
            input_tokens=turn.input_tokens,
            output_tokens=turn.output_tokens,
        )

    (cell_dir / "answer.json").write_text(
        json.dumps(
            {
                "question_id": question.question_id,
                "agent_id": agent_id,
                "final_answer": cell.final_answer,
                "final_text": cell.final_text,
                "ideal": question.ideal,
                "eval_mode": question.eval_mode,
            },
            indent=2,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    (cell_dir / "grade.json").write_text(
        json.dumps(asdict(cell), indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    return cell


def write_report(project_root: Path, run_id: str, results: list[CellResult]) -> Path:
    """Dump a flat CSV + markdown summary under `reports/<run_id>/`."""

    import csv

    rep_dir = project_root / "reports" / run_id
    rep_dir.mkdir(parents=True, exist_ok=True)
    csv_path = rep_dir / "matrix.csv"
    fields = list(CellResult.__dataclass_fields__.keys())
    with csv_path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        for r in results:
            row = asdict(r)
            row["categories"] = ",".join(r.categories)
            w.writerow(row)

    by_agent: dict[str, list[CellResult]] = {}
    for r in results:
        by_agent.setdefault(r.agent_id, []).append(r)
    lines = [f"# omicos-bixbench run `{run_id}`\n"]
    lines.append("| agent | answered | correct | accuracy |")
    lines.append("|---|---:|---:|---:|")
    for agent_id, rs in sorted(by_agent.items()):
        n = len(rs)
        c = sum(1 for r in rs if r.correct)
        acc = c / n if n else 0.0
        lines.append(f"| `{agent_id}` | {n} | {c} | {acc:.1%} |")
    (rep_dir / "summary.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return rep_dir
