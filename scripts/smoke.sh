#!/usr/bin/env bash
# End-to-end smoke test on a single (question, agent) pair.
# Usage: bash scripts/smoke.sh [agent_id] [question_id]

set -euo pipefail

cd "$(dirname "$0")/.."

# Load secrets if not already exported.
if [[ -f "$HOME/.claude/secrets.env" ]]; then
  set -a; source "$HOME/.claude/secrets.env"; set +a
fi

: "${HF_TOKEN:?HF_TOKEN missing (need access to phylobio/BixBench-Verified-50)}"
: "${DEEPSEEK_API_KEY:?DEEPSEEK_API_KEY missing}"

if [[ ! -d .venv ]]; then
  uv sync
fi

AGENT="${1:-omicverse_omni}"
shift || true
QID_FLAG=""
if [[ $# -gt 0 ]]; then
  QID_FLAG="--qid $1"
fi

uv run omicos-bixbench smoke --agent "$AGENT" $QID_FLAG
