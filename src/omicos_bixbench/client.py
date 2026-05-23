"""SSE client for `POST /api/agent/chat/stream`.

Drains until `{"type":"done"}` or `{"type":"error"}`, returns the final
assistant text plus a compact trajectory (token usage, tool-call counts,
raw event log path).
"""

from __future__ import annotations

import json
import re
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx
from httpx_sse import connect_sse

FINAL_ANSWER_RE = re.compile(
    r"FINAL\s*ANSWER\s*[:\-]\s*(.+?)\s*$",
    re.IGNORECASE | re.DOTALL | re.MULTILINE,
)


@dataclass
class TurnResult:
    final_text: str
    final_answer: str  # extracted via FINAL ANSWER marker; falls back to final_text
    events: int = 0
    tool_calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    error: str | None = None
    sse_log: Path | None = None
    raw_assistant_segments: list[str] = field(default_factory=list)
    elapsed_s: float = 0.0


def _extract_final_answer(text: str) -> str:
    m = FINAL_ANSWER_RE.search(text)
    if m:
        return m.group(1).strip()
    return text.strip()


def _assistant_text_from_step(step: Any) -> str:
    """Heuristic extractor — omicos's `step` payload is a persisted message
    record. Different code paths in omicos-core have used slightly different
    shapes over time, so we accept the most common ones."""

    if not isinstance(step, dict):
        return ""
    role = step.get("role") or step.get("author") or ""
    if role and role != "assistant":
        return ""
    content = step.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for item in content:
            if isinstance(item, dict):
                if isinstance(item.get("text"), str):
                    parts.append(item["text"])
                elif isinstance(item.get("content"), str):
                    parts.append(item["content"])
            elif isinstance(item, str):
                parts.append(item)
        return "".join(parts)
    if isinstance(content, dict) and isinstance(content.get("text"), str):
        return content["text"]
    if isinstance(step.get("text"), str):
        return step["text"]
    return ""


def run_turn(
    *,
    base_url: str,
    agent_id: str,
    user_message: str,
    model_cfg: dict,
    sse_log: Path | None = None,
    timeout_s: float = 1800.0,
) -> TurnResult:
    """Open one streamed chat turn and block until terminal.

    `model_cfg` is the parsed `agent_model` block from `configs/models.yaml`.
    """

    session_id = str(uuid.uuid4())
    config = {
        "provider": model_cfg.get("provider", "custom_openai"),
        "model": model_cfg.get("model", "deepseek-v4-pro"),
        "agent": agent_id,
        # `team_members: [agent_id]` collapses the prompt's team roster
        # to just the active agent. Without this, omicos-core defaults
        # to "team = full admin catalog" (26 agents), which adds ~7.5k
        # chars of sibling-agent summaries between OmicOS Core's
        # operational preamble and the active agent's body — pushing the
        # agent's `Rule of iron #0` from ~5% to ~26% of system prompt
        # depth and visibly degrading rule compliance on DeepSeek-v4-pro.
        # We don't need cross-agent routing in this benchmark; each cell
        # is single-agent.
        "team_members": [agent_id],
        "permission_mode": model_cfg.get("permission_mode", "full"),
        "allow_shell": bool(model_cfg.get("allow_shell", True)),
        "allow_file_write": bool(model_cfg.get("allow_file_write", True)),
        "max_tool_iterations": int(model_cfg.get("max_tool_iterations", 30)),
        "model_context_window": int(model_cfg.get("model_context_window", 1_000_000)),
    }
    # Intentionally omit `temperature` when the config does not pin one —
    # let the upstream provider use its server-side default.
    if "temperature" in model_cfg:
        config["temperature"] = float(model_cfg["temperature"])

    body = {"message": user_message, "config": config}
    headers = {
        "content-type": "application/json",
        "accept": "text/event-stream",
        "x-agent-session-id": session_id,
    }

    result = TurnResult(final_text="", final_answer="", sse_log=sse_log)
    log_fh = sse_log.open("w", encoding="utf-8") if sse_log else None
    started = time.monotonic()
    try:
        with httpx.Client(timeout=httpx.Timeout(timeout_s, read=timeout_s)) as client:
            with connect_sse(
                client,
                "POST",
                f"{base_url}/api/agent/chat/stream",
                json=body,
                headers=headers,
            ) as event_source:
                buf: list[str] = []
                for ev in event_source.iter_sse():
                    if not ev.data:
                        continue
                    if log_fh:
                        log_fh.write(ev.data + "\n")
                    try:
                        payload = json.loads(ev.data)
                    except json.JSONDecodeError:
                        continue
                    result.events += 1
                    et = payload.get("type")
                    if et == "llm_chunk":
                        c = payload.get("content")
                        if isinstance(c, str):
                            buf.append(c)
                    elif et == "step":
                        content = payload.get("content") or {}
                        # `step` events of role="step" carry the executed
                        # `tool_calls` array for the just-finished tool turn.
                        # Use THAT for the real call count — `tool_delta`
                        # events are streamed-arg chunks (one per token of
                        # the call's JSON args), so counting them inflates
                        # the metric by ~100×.
                        if isinstance(content, dict) and content.get("role") == "step":
                            tcs = content.get("tool_calls") or []
                            if isinstance(tcs, list):
                                result.tool_calls += len(tcs)
                        text = _assistant_text_from_step(content)
                        if text:
                            # A persisted assistant message often supersedes
                            # the streamed chunks for that segment — keep
                            # both and reconcile at the end.
                            result.raw_assistant_segments.append(text)
                            buf = []
                    elif et == "usage":
                        u = payload.get("content") or {}
                        result.input_tokens += int(u.get("input_tokens") or 0)
                        result.output_tokens += int(u.get("output_tokens") or 0)
                    elif et == "error":
                        result.error = str(payload.get("content") or "unknown error")
                        break
                    elif et == "done":
                        if buf:
                            result.raw_assistant_segments.append("".join(buf))
                        break
    finally:
        result.elapsed_s = time.monotonic() - started
        if log_fh:
            log_fh.close()

    final_text = ""
    if result.raw_assistant_segments:
        final_text = result.raw_assistant_segments[-1].strip()
    result.final_text = final_text
    result.final_answer = _extract_final_answer(final_text)
    return result
