"""`omicos-bixbench` CLI.

Subcommands:

    fetch           Download the dataset + all 33 capsule zips into ./data/
    smoke           Run ONE (question, agent) cell end-to-end as a sanity check
    run [opts]      Run the full agent × question matrix
    report <run>    Re-emit matrix.csv + summary.md from per-cell grade.json
                    files (e.g. after re-running the grader)

All paths default to the project root containing this `pyproject.toml`.
"""

from __future__ import annotations

import datetime as _dt
import json
import os
import sys
from dataclasses import asdict
from pathlib import Path

import click
import yaml

from . import dataset as ob_dataset
from . import matrix as ob_matrix


def _project_root() -> Path:
    # src/omicos_bixbench/cli.py → src/omicos_bixbench → src → project root.
    return Path(__file__).resolve().parents[2]


def _new_run_id() -> str:
    return _dt.datetime.now().strftime("run-%Y%m%d-%H%M%S")


@click.group()
def main() -> None:
    """Evaluate omicos-core agents on BixBench-Verified-50."""


@main.command()
def fetch() -> None:
    """Download the JSONL index and all capsule zips into the HF cache."""

    questions = ob_dataset.load_questions()
    click.echo(f"index: {len(questions)} questions")
    seen: set[str] = set()
    for q in questions:
        if q.data_folder in seen:
            continue
        seen.add(q.data_folder)
        path = ob_dataset.fetch_capsule_zip(q)
        click.echo(f"  ok {q.data_folder}  → {path}")
    click.echo(f"done: {len(seen)} capsules cached")


@main.command()
@click.option("--agent", default="omicverse_omni", show_default=True,
              help="Agent id to smoke-test.")
@click.option("--qid", default=None,
              help="Specific question_id; defaults to the first row.")
def smoke(agent: str, qid: str | None) -> None:
    """Run ONE cell end-to-end. Validates omicos binary, env, SSE drain, grader."""

    project = _project_root()
    agents_yaml = project / "configs" / "agents.yaml"
    models_yaml = project / "configs" / "models.yaml"
    questions = ob_dataset.load_questions()
    if qid:
        questions = [q for q in questions if q.question_id == qid]
        if not questions:
            click.echo(f"no question with id {qid!r}", err=True)
            sys.exit(2)
    else:
        questions = questions[:1]

    # Stuff `agents.yaml` with just the one agent so the matrix code path is
    # identical to a full run.
    one_agent_yaml = project / "results" / "_smoke_agents.yaml"
    one_agent_yaml.parent.mkdir(parents=True, exist_ok=True)
    one_agent_yaml.write_text(
        yaml.safe_dump({"agents": [{"id": agent, "tier": "any", "categories": []}]}),
        encoding="utf-8",
    )

    run_id = "smoke-" + _dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    results = ob_matrix.run_matrix(
        project_root=project,
        run_id=run_id,
        agents_yaml=one_agent_yaml,
        models_yaml=models_yaml,
        questions=questions,
    )
    rep_dir = ob_matrix.write_report(project, run_id, results)
    for r in results:
        click.echo(json.dumps(asdict(r), indent=2, ensure_ascii=False))
    click.echo(f"\nreport: {rep_dir}")


@main.command()
@click.option("--agents", default=None,
              help="Comma-separated agent ids; overrides configs/agents.yaml ids.")
@click.option("--limit", type=int, default=None,
              help="Cap the number of questions (top-N from the JSONL).")
@click.option("--qids", default=None,
              help="Comma-separated explicit question_ids to run.")
@click.option("--categories", default=None,
              help="Comma-separated category filter (any-match across the row).")
@click.option("--run-id", default=None,
              help="Override the generated run id.")
@click.option("--concurrency", "-j", type=int, default=1, show_default=True,
              help="Number of cells to run in parallel. Each cell spawns its own "
                   "omicos serve + Python kernel, so RAM budget is ~1-3 GB per "
                   "concurrent worker once data is loaded.")
def run(agents: str | None,
        limit: int | None,
        qids: str | None,
        categories: str | None,
        run_id: str | None,
        concurrency: int) -> None:
    """Run the full agent × question matrix."""

    project = _project_root()
    models_yaml = project / "configs" / "models.yaml"
    agents_yaml = project / "configs" / "agents.yaml"

    if agents:
        ids = [a.strip() for a in agents.split(",") if a.strip()]
        override = project / "results" / "_cli_agents.yaml"
        override.parent.mkdir(parents=True, exist_ok=True)
        override.write_text(
            yaml.safe_dump({
                "agents": [{"id": a, "tier": "any", "categories": []} for a in ids],
            }),
            encoding="utf-8",
        )
        agents_yaml = override

    all_q = ob_dataset.load_questions()
    qs = list(ob_dataset.iter_questions_filtered(
        all_q,
        only_ids=[q.strip() for q in (qids or "").split(",") if q.strip()] or None,
        only_categories=[c.strip() for c in (categories or "").split(",") if c.strip()] or None,
    ))
    if limit is not None:
        qs = qs[:limit]
    if not qs:
        click.echo("no questions match the filters", err=True)
        sys.exit(2)

    rid = run_id or _new_run_id()
    click.echo(f"run_id={rid}  questions={len(qs)}")
    results = ob_matrix.run_matrix(
        project_root=project,
        run_id=rid,
        agents_yaml=agents_yaml,
        models_yaml=models_yaml,
        questions=qs,
        concurrency=concurrency,
    )
    rep_dir = ob_matrix.write_report(project, rid, results)
    correct = sum(1 for r in results if r.correct)
    click.echo(f"\n{len(results)} cells  |  {correct} correct  |  report: {rep_dir}")


@main.command()
@click.argument("run_id")
@click.option("--no-llm", is_flag=True, default=False,
              help="Skip llm_verifier rows (those still need a network call). "
                   "Use when you only want to re-apply the new str/range "
                   "tolerance to deterministic verifiers.")
def regrade(run_id: str, no_llm: bool) -> None:
    """Re-grade an existing run with the current `grader.py` rules.

    Reads each cell's `answer.json` (which preserved `final_answer`,
    `ideal`, `eval_mode`, and `question_id`), re-invokes `grader.grade()`,
    and overwrites `grade.json` in place. Useful after tightening or
    loosening the str/range/llm tolerance — you don't have to re-drive
    the agents through DeepSeek again.
    """

    import yaml as _yaml
    from . import grader as ob_grader

    project = _project_root()
    run_root = project / "results" / run_id
    if not run_root.is_dir():
        click.echo(f"no run dir at {run_root}", err=True)
        sys.exit(2)

    models_cfg = _yaml.safe_load((project / "configs" / "models.yaml").read_text()) or {}
    judge_model = models_cfg.get("judge_model", {})
    questions = {q.question_id: q for q in ob_dataset.load_questions()}

    flipped = 0
    total = 0
    for answer_path in sorted(run_root.glob("*/*/answer.json")):
        data = json.loads(answer_path.read_text(encoding="utf-8"))
        qid = data.get("question_id")
        q = questions.get(qid)
        if q is None:
            continue
        if no_llm and data.get("eval_mode") == "llm_verifier":
            continue
        grade_path = answer_path.with_name("grade.json")
        prior = json.loads(grade_path.read_text(encoding="utf-8")) if grade_path.exists() else {}
        new_grade = ob_grader.grade(
            eval_mode=data.get("eval_mode", q.eval_mode),
            agent_answer=data.get("final_answer", ""),
            ideal=data.get("ideal", q.ideal),
            question=q.question,
            judge_cfg=judge_model,
        )
        total += 1
        was = bool(prior.get("correct", False))
        now = bool(new_grade.correct)
        if was != now:
            flipped += 1
        merged = dict(prior)
        merged["correct"] = now
        merged["score"] = new_grade.score
        merged["grader_notes"] = new_grade.notes
        grade_path.write_text(json.dumps(merged, indent=2, ensure_ascii=False), encoding="utf-8")
        if was != now:
            click.echo(
                f"  FLIP {data.get('agent_id')}/{qid}  "
                f"{was} -> {now}  ({new_grade.notes[:80]})"
            )
    click.echo(f"\nregraded {total} cells, {flipped} verdict flips")
    rep_dir = ob_matrix.write_report(
        project,
        run_id,
        [ob_matrix.CellResult(**json.loads(p.read_text(encoding="utf-8")))
         for p in run_root.glob("*/*/grade.json")],
    )
    click.echo(f"report: {rep_dir}")


@main.command()
@click.argument("run_id")
def report(run_id: str) -> None:
    """Regenerate matrix.csv + summary.md from existing grade.json files.

    Useful when re-running the grader logic without re-driving the agents.
    """

    project = _project_root()
    run_root = project / "results" / run_id
    if not run_root.is_dir():
        click.echo(f"no run dir at {run_root}", err=True)
        sys.exit(2)
    results: list[ob_matrix.CellResult] = []
    for grade_path in run_root.glob("*/*/grade.json"):
        data = json.loads(grade_path.read_text(encoding="utf-8"))
        data["categories"] = data.get("categories") or []
        results.append(ob_matrix.CellResult(**data))
    rep_dir = ob_matrix.write_report(project, run_id, results)
    click.echo(f"report: {rep_dir}  ({len(results)} cells)")


if __name__ == "__main__":  # pragma: no cover
    main()
