"""Per-task `omicos serve` lifecycle manager.

Each cell of the (agent × question) matrix gets its own:
  * workspace dir (capsule unpack target)
  * `<workspace>/agents/` overlay symlinked to the admin catalog so the
    requested agent_id is resolvable offline
  * unique TCP port
  * fresh subprocess

omicos-core's `acquire_serve_lock` enforces one-daemon-per-workspace, so
running matrix cells in parallel against the SAME capsule means we have
to materialize the workspace once per agent — handled by the orchestrator
that calls `stage_question(...)` with a `<run_id>/<agent_id>/` prefix.
"""

from __future__ import annotations

import os
import shutil
import socket
import subprocess
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

import httpx


def _free_port() -> int:
    """Pick an ephemeral port. Tiny race window — acceptable for a research
    harness, not for production."""

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@dataclass
class OmicosProcess:
    proc: subprocess.Popen
    port: int
    workspace: Path
    data_dir: Path
    log_path: Path

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.port}"


def _resolve_omicos_bin() -> str:
    explicit = os.environ.get("OMICOS_BIN")
    if explicit and Path(explicit).is_file():
        return explicit
    found = shutil.which("omicos")
    if found:
        return found
    raise RuntimeError(
        "Cannot locate the omicos binary. Build with "
        "`cargo build --release` inside omicos-core and either add the "
        "target/release dir to PATH or export OMICOS_BIN=<abs path>."
    )


DEFAULT_ADMIN_AGENTS_DIR = (
    os.path.expanduser("~/omicverse/omicos-admin/agents")
)
DEFAULT_RUNTIME_AGENTS_DIR = (
    os.path.expanduser("~/omicverse/omicos-runtime/")
    ".omicos/cloud-agents/agents"
)


def _resolve_templates_dir(workspace: Path) -> Path:
    """Build (or reuse) a per-run agents catalog and return its path.

    omicos-core has two places a workspace could pick up agents:

      1. `<workspace>/agents/` — the *workspace overlay*. Gated on
         `lab` tier-or-higher in `workspace_extensions::can_use_workspace_extensions`,
         so it returns empty in an offline / community-tier run.
      2. `OMICOS_TEMPLATES_DIR` — the *base templates dir*. NOT tier-gated;
         this is the cloud-catalog replacement and is what `default_templates_dir`
         picks up first. Everything we put here is visible to every tier.

    We want (1)'s workspace-locality but (2)'s tier-blindness, so we
    materialize a single merged catalog at
    `<workspace>/_agents_catalog/agents/` containing the union of the
    admin catalog + the runtime cloud-agents cache (the latter is where
    the `omicverse_expert` / `omicverse_spatial` .md files live), and
    point `OMICOS_TEMPLATES_DIR` at `<workspace>/_agents_catalog`.
    Important: omicos-core's `TemplateStore` joins `agents/` onto the
    templates dir (`agents.rs:344  self.root.join("agents")`), so the
    env var must name the *parent* of the agents subdir, not the
    subdir itself.

    Admin entries win on filename collision (admin is the canonical
    source for any agent that exists in both places).
    """

    admin = Path(os.environ.get(
        "OMICOS_BIXBENCH_AGENTS_DIR", DEFAULT_ADMIN_AGENTS_DIR,
    ))
    runtime = Path(os.environ.get(
        "OMICOS_BIXBENCH_RUNTIME_AGENTS_DIR", DEFAULT_RUNTIME_AGENTS_DIR,
    ))
    if not admin.is_dir():
        raise RuntimeError(
            f"Agent catalog dir not found: {admin}. "
            "Clone omicos-admin or override OMICOS_BIXBENCH_AGENTS_DIR."
        )

    root = workspace / "_agents_catalog"
    agents_subdir = root / "agents"
    if root.exists():
        shutil.rmtree(root)
    agents_subdir.mkdir(parents=True)

    # Order matters: admin first (canonical), runtime fills in lab-tier
    # agents that admin doesn't ship (omicverse_expert, omicverse_spatial).
    seen: set[str] = set()
    for src in (admin, runtime):
        if not src.is_dir():
            continue
        for f in sorted(src.iterdir()):
            if not (f.is_file() and f.suffix == ".md"):
                continue
            if f.name in seen:
                continue
            shutil.copyfile(f, agents_subdir / f.name)
            seen.add(f.name)
    if not seen:
        raise RuntimeError(
            f"No agent .md files found under {admin} or {runtime}"
        )
    return root


def _provider_env() -> dict[str, str]:
    """Translate DEEPSEEK_* into the env vars omicos-core's custom_openai
    provider reads, and force-disable cloud sync so the run is deterministic."""

    key = os.environ.get("DEEPSEEK_API_KEY")
    base = os.environ.get("DEEPSEEK_API_BASE", "https://api.deepseek.com/v1")
    if not key:
        raise RuntimeError(
            "DEEPSEEK_API_KEY not set. `source ~/.claude/secrets.env` first."
        )
    return {
        "CUSTOM_OPENAI_API_BASE": base,
        "CUSTOM_OPENAI_API_KEY": key,
        # Also expose under deepseek-native names — the catalog-driven
        # `provider=deepseek` path reads these.
        "DEEPSEEK_API_BASE": base,
        "DEEPSEEK_API_KEY": key,
        "OMICOS_AGENTS_OFFLINE": "1",
        "OMICOS_SKILLS_OFFLINE": "1",
        "OMICOS_MODELS_OFFLINE": "1",
        "OMICOS_MEMORY_OFFLINE": "1",
    }


def _wait_for_health(base_url: str, timeout_s: float = 60.0) -> None:
    deadline = time.monotonic() + timeout_s
    last_err: Exception | None = None
    while time.monotonic() < deadline:
        try:
            r = httpx.get(f"{base_url}/health", timeout=2.0)
            if r.status_code == 200:
                return
        except Exception as e:
            last_err = e
        time.sleep(0.5)
    raise RuntimeError(
        f"omicos serve did not become healthy at {base_url} "
        f"within {timeout_s:.0f}s (last error: {last_err})"
    )


@contextmanager
def serve(
    workspace: Path,
    *,
    log_path: Path | None = None,
    extra_env: dict[str, str] | None = None,
    health_timeout_s: float = 60.0,
) -> Iterator[OmicosProcess]:
    """Spawn `omicos serve` against `workspace`, yield the live process,
    tear it down on exit. SIGTERM → 5s grace → SIGKILL."""

    workspace = workspace.resolve()
    if not workspace.is_dir():
        raise RuntimeError(f"workspace does not exist: {workspace}")
    templates_dir = _resolve_templates_dir(workspace)

    port = _free_port()
    data_dir = workspace / ".omicos"
    data_dir.mkdir(exist_ok=True)
    log_path = log_path or (workspace / "omicos.log")

    # Skill catalog: point omicos-core's discovery at the admin source of
    # truth so newly-deployed cloud skills (e.g. sample-metadata-alignment)
    # surface in the agent's `## Available skills` roster. Otherwise
    # OMICOS_SKILLS_OFFLINE=1 leaves the catalog empty.
    skill_roots = os.environ.get(
        "OMICOS_BIXBENCH_SKILLS_DIR",
        str(Path(DEFAULT_ADMIN_AGENTS_DIR).parent / "skills"),
    )

    env = {
        **os.environ,
        **_provider_env(),
        "OMICOS_TEMPLATES_DIR": str(templates_dir),
        "OMICOS_SKILL_ROOTS": skill_roots,
        **(extra_env or {}),
    }
    cmd = [
        _resolve_omicos_bin(),
        "serve",
        "--host",
        "127.0.0.1",
        "--port",
        str(port),
        "--data-dir",
        str(data_dir),
        "--no-browser",
    ]
    log_fh = log_path.open("w", encoding="utf-8")
    proc = subprocess.Popen(
        cmd,
        cwd=str(workspace),
        env=env,
        stdout=log_fh,
        stderr=subprocess.STDOUT,
    )
    try:
        _wait_for_health(f"http://127.0.0.1:{port}", timeout_s=health_timeout_s)
        yield OmicosProcess(
            proc=proc,
            port=port,
            workspace=workspace,
            data_dir=data_dir,
            log_path=log_path,
        )
    finally:
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=5)
        log_fh.close()
