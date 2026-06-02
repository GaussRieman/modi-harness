"""Modi Harness — Code Auditor demo.

Runs an Anthropic Claude agent over the modi-harness src/ tree. The agent
discovers the largest Python files via `list_python_files`, reads them via
`read_file`, then produces a Markdown audit report.

Demonstrates:
- Multi-provider Model Adapter (reads .env for provider + key)
- Live token-by-token streaming via `astream` + `rich`
- Tool gateway with structured tool calls
- A real, end-to-end task that's actually useful

Run from the repo root:
    uv run python examples/code_auditor/run.py
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

from rich.console import Console

from modi_harness import ModiHarness
from modi_harness.cli.runner import run_streaming
from modi_harness.config import Settings
from modi_harness.models import create_chat_model

# ---------------------------------------------------------------------------
# Tool implementations
# ---------------------------------------------------------------------------

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
SRC_DIR = REPO_ROOT / "src" / "modi_harness"


def _safe_path(rel: str) -> Path:
    """Resolve a path under SRC_DIR, blocking traversal."""
    target = (SRC_DIR / rel).resolve()
    if not str(target).startswith(str(SRC_DIR.resolve())):
        raise ValueError(f"path escapes src/: {rel}")
    return target


def list_python_files(directory: str = "") -> dict:
    """List Python files under src/modi_harness/ with line counts."""
    base = _safe_path(directory)
    if not base.exists():
        return {"error": f"not found: {directory!r}"}
    files = []
    for path in sorted(base.rglob("*.py")):
        try:
            with path.open(encoding="utf-8") as fh:
                lines = sum(1 for _ in fh)
        except OSError:
            lines = -1
        rel = path.relative_to(SRC_DIR)
        files.append({"path": str(rel), "lines": lines})
    files.sort(key=lambda f: f["lines"], reverse=True)
    return {"files": files, "count": len(files)}


def read_file(path: str, max_lines: int = 200) -> dict:
    """Read a Python file under src/modi_harness/, truncated to max_lines."""
    target = _safe_path(path)
    if not target.exists() or not target.is_file():
        return {"error": f"not a file: {path!r}"}
    text = target.read_text(encoding="utf-8")
    lines = text.splitlines()
    shown = min(len(lines), max_lines)
    return {
        "path": path,
        "total_lines": len(lines),
        "shown_lines": shown,
        "content": "\n".join(lines[:max_lines]),
    }


# ---------------------------------------------------------------------------
# Tool specs
# ---------------------------------------------------------------------------

LIST_PY_FILES_SPEC = {
    "name": "list_python_files",
    "description": "List Python files under src/modi_harness/ with line counts. Returns files sorted largest-first.",
    "input_schema": {
        "type": "object",
        "properties": {
            "directory": {
                "type": "string",
                "description": "Subdirectory under src/modi_harness/ (e.g., 'graph') or '' for the root.",
            },
        },
    },
    "risk_level": "L0",
    "side_effect": False,
}

READ_FILE_SPEC = {
    "name": "read_file",
    "description": "Read a Python file under src/modi_harness/. Content is truncated to max_lines.",
    "input_schema": {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "Path relative to src/modi_harness/ (e.g., 'runtime/adapter.py').",
            },
            "max_lines": {
                "type": "integer",
                "description": "Max lines to return (default 200).",
            },
        },
        "required": ["path"],
    },
    "risk_level": "L0",
    "side_effect": False,
}


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

async def main() -> int:
    console = Console()
    console.print()
    console.print("[bold cyan]Modi Harness — Code Auditor[/bold cyan]")
    console.print("[dim]Auditing src/modi_harness/ with the configured model[/dim]")
    console.print()

    settings = Settings()
    if not settings.model.api_key:
        console.print("[red]Error:[/red] MODI_MODEL_API_KEY not set in .env")
        console.print("[dim]Copy .env.example to .env and fill in your API key.[/dim]")
        return 1

    console.print(
        f"[dim]Provider:[/dim] {settings.model.provider}  "
        f"[dim]Model:[/dim] {settings.model.name or '(default)'}"
    )
    console.print()

    chat_model = create_chat_model(
        provider=settings.model.provider,
        name=settings.model.name,
        api_key=settings.model.api_key,
        base_url=settings.model.base_url,
    )

    agents_dir = Path(__file__).parent / "agents"
    harness = ModiHarness(
        agents_dir=str(agents_dir),
        chat_model=chat_model,
        max_steps=40,
    )
    harness.register_tool(LIST_PY_FILES_SPEC, list_python_files)
    harness.register_tool(READ_FILE_SPEC, read_file)

    return await run_streaming(
        harness,
        agent="code-auditor",
        input={
            "goal": "Audit the modi-harness Python codebase",
            "messages": [
                {
                    "role": "user",
                    "content": (
                        "Audit src/modi_harness/. List the top 5 largest Python files, "
                        "read each one, and produce a Markdown report with a quality "
                        "score (1-10) and one specific improvement suggestion per file. "
                        "End with a brief overall assessment."
                    ),
                }
            ],
        },
        permission_mode="auto",
        console=console,
    )


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
