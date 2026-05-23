"""HuggingFace loader + capsule staging for BixBench-Verified-50.

The dataset ships:
  * `BixBench-Verified-50.jsonl` — 50 questions, fields:
      question_id, question, ideal, distractors[], eval_mode (str_verifier
      | range_verifier | llm_verifier), capsule_uuid, data_folder
      (`CapsuleFolder-<uuid>.zip`), categories, paper, hypothesis, result,
      answer (bool), canary.
  * `CapsuleFolder-<uuid>.zip` — per-capsule bundle with `CapsuleData-<uuid>/`
    (agent-visible inputs) and `CapsuleNotebook-<uuid>/` (held-out expert
    reference; never staged into the agent's workspace).

`stage_question(...)` unpacks the data half into a fresh workspace, returns
its path. The notebook half is kept under `data/notebooks/` for graders to
peek at if needed but is NOT linked into the workspace.
"""

from __future__ import annotations

import ast
import json
import os
import shutil
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterator

from huggingface_hub import hf_hub_download, login

DATASET_REPO_ID = "phylobio/BixBench-Verified-50"
JSONL_FILENAME = "BixBench-Verified-50.jsonl"


def _coerce_list(value) -> list[str]:
    """The JSONL ships some array-valued fields (categories, distractors) as
    Python-repr strings, e.g. `"['WGS', 'Genomics']"`. Accept that form, the
    JSON-encoded form, the native list form, and bare strings."""

    if value is None:
        return []
    if isinstance(value, list):
        return [str(v).strip() for v in value if str(v).strip()]
    if not isinstance(value, str):
        return [str(value).strip()] if str(value).strip() else []
    s = value.strip()
    if not s:
        return []
    # Try JSON first (handles double-quoted lists); fall through to
    # Python-repr (single-quoted lists / tuples).
    for loader in (json.loads, ast.literal_eval):
        try:
            parsed = loader(s)
        except Exception:
            continue
        if isinstance(parsed, (list, tuple)):
            return [str(v).strip() for v in parsed if str(v).strip()]
        if isinstance(parsed, str):
            return [parsed.strip()] if parsed.strip() else []
    # Last resort: comma-split.
    return [p.strip() for p in s.split(",") if p.strip()]


@dataclass
class Question:
    question_id: str
    question: str
    ideal: str
    distractors: list[str]
    eval_mode: str  # "str_verifier" | "range_verifier" | "llm_verifier"
    capsule_uuid: str
    data_folder: str  # e.g. "CapsuleFolder-<uuid>.zip"
    categories: list[str]
    paper: str = ""
    hypothesis: str = ""
    result: str = ""
    answer: bool | None = None
    canary: str = ""
    raw: dict = field(default_factory=dict)

    @classmethod
    def from_jsonl_row(cls, row: dict) -> "Question":
        cats = _coerce_list(row.get("categories"))
        distractors = _coerce_list(row.get("distractors"))
        return cls(
            question_id=str(row["question_id"]),
            question=str(row["question"]),
            ideal=str(row["ideal"]),
            distractors=[str(d) for d in distractors],
            eval_mode=str(row.get("eval_mode", "llm_verifier")),
            capsule_uuid=str(row.get("capsule_uuid", "")),
            data_folder=str(row.get("data_folder", "")),
            categories=cats,
            paper=str(row.get("paper", "")),
            hypothesis=str(row.get("hypothesis", "")),
            result=str(row.get("result", "")),
            answer=row.get("answer"),
            canary=str(row.get("canary", "")),
            raw=row,
        )


def _data_dir() -> Path:
    base = os.environ.get(
        "OMICOS_BIXBENCH_DATA_DIR",
        str(Path(__file__).resolve().parents[2] / "data"),
    )
    p = Path(base)
    p.mkdir(parents=True, exist_ok=True)
    return p


def _login_if_token() -> None:
    """`huggingface_hub.login` is idempotent and silent on rerun. The dataset
    is gated, so without a token `hf_hub_download` errors out — fail loud at
    the boundary so the user knows what to fix."""

    token = os.environ.get("HF_TOKEN") or os.environ.get("HUGGINGFACE_TOKEN")
    if not token:
        raise RuntimeError(
            "BixBench-Verified-50 is a gated HF dataset. "
            "Export HF_TOKEN (e.g. `source ~/.claude/secrets.env`)."
        )
    login(token=token, add_to_git_credential=False)


def fetch_index() -> Path:
    """Download `BixBench-Verified-50.jsonl` and return the local path."""

    _login_if_token()
    local = hf_hub_download(
        repo_id=DATASET_REPO_ID,
        filename=JSONL_FILENAME,
        repo_type="dataset",
        cache_dir=str(_data_dir() / "hf-cache"),
    )
    return Path(local)


def load_questions() -> list[Question]:
    """Parse the JSONL into typed `Question` rows."""

    path = fetch_index()
    out: list[Question] = []
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            out.append(Question.from_jsonl_row(json.loads(line)))
    return out


def fetch_capsule_zip(q: Question) -> Path:
    """Download (or read from cache) the capsule zip for a question."""

    _login_if_token()
    local = hf_hub_download(
        repo_id=DATASET_REPO_ID,
        filename=q.data_folder,
        repo_type="dataset",
        cache_dir=str(_data_dir() / "hf-cache"),
    )
    return Path(local)


def stage_question(q: Question, cell_dir: Path) -> Path:
    """Unpack the capsule's DATA half into `<cell_dir>/workspace/`. Returns
    the workspace path that `omicos serve` should be launched against.

    The expert reference notebook (`CapsuleNotebook-<uuid>/`) is split off
    into `<cell_dir>/_notebook_holdout/` so it is visible to the grader
    but invisible to the agent.

    `cell_dir` is expected to be the per-cell directory (`results/<run_id>/
    <agent_id>/<qid>/`); we do not append the qid again to avoid the
    `.../<qid>/<qid>/workspace/` nesting earlier versions produced.
    """

    zip_path = fetch_capsule_zip(q)
    workspace = cell_dir / "workspace"
    holdout = cell_dir / "_notebook_holdout"
    if workspace.exists():
        shutil.rmtree(workspace)
    if holdout.exists():
        shutil.rmtree(holdout)
    workspace.mkdir(parents=True)
    holdout.mkdir(parents=True)

    with zipfile.ZipFile(zip_path, "r") as zf:
        for name in zf.namelist():
            target_root = holdout if "CapsuleNotebook-" in name else workspace
            zf.extract(name, target_root)
    return workspace


def iter_questions_filtered(
    questions: list[Question],
    only_ids: list[str] | None = None,
    only_categories: list[str] | None = None,
) -> Iterator[Question]:
    """Filter by explicit id list and/or category intersection."""

    cat_set = {c.lower() for c in (only_categories or [])}
    for q in questions:
        if only_ids and q.question_id not in only_ids:
            continue
        if cat_set and not (cat_set & {c.lower() for c in q.categories}):
            continue
        yield q
