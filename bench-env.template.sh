#!/usr/bin/env bash
# Template for OmicOS-BixBench. Copy to bench-env.sh, fill in the
# secrets, then `source bench-env.sh` before running any sweep.

# === omicos binary path ===
# Point to your locally-installed `omicos` executable. After
# `pip install omicos` (or building from github.com/omicverse/omicos)
# this should be on PATH. Optionally pin a specific build:
# export OMICOS_BIN="$HOME/.local/bin/omicos"

# === Hugging Face — required to download BixBench-Verified-50 ===
# Dataset is gated; accept the terms at
# https://huggingface.co/datasets/phylobio/BixBench-Verified-50 first.
export HF_TOKEN="<your_hf_token_here>"

# Where the dataset snapshot lives. Default: $(pwd)/data/bixbench-verified-50
# export OMICOS_BIXBENCH_DATA_ROOT="$(pwd)/data/bixbench-verified-50"

# === LLM provider API keys ===
# omicos agent backend — pick one set:
#
# (a) DeepSeek API (default in configs/models.yaml before 2026-05-18):
# export DEEPSEEK_API_KEY="<your_deepseek_key>"
# export DEEPSEEK_API_BASE="https://api.deepseek.com/v1"
#
# (b) ChatGPT subscription via Codex CLI OAuth (current default —
#     gpt-5.5 is the model the headline run used):
# Log in once with `codex login`; auth lands at ~/.codex/auth.json.
#     No env var needed for the agent backend if Codex tokens are present.

# Optional grader judge key (LLM verifier uses the agent's same model
# by default; override:
# export OMICOS_BIXBENCH_JUDGE_MODEL="deepseek-v4-pro"

# === Agent catalog (omicos-admin) ===
# Path to the omicos-admin checkout's agents/ directory. Optional —
# defaults to ~/omicverse/omicos-admin/agents.
# export OMICOS_BIXBENCH_AGENTS_DIR="$HOME/omicverse/omicos-admin/agents"

echo "[bench-env] sourced — omicos binary at: $(command -v omicos || echo 'NOT FOUND, see https://github.com/omicverse/omicos')"
