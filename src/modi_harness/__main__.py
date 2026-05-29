"""CLI smoke entry. Lightweight V0.2 CLI for running a sample task."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import __version__


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv

    if args and args[0] in {"-V", "--version"}:
        print(__version__)
        return 0

    parser = argparse.ArgumentParser(prog="modi", description="Modi Harness CLI")
    sub = parser.add_subparsers(dest="cmd")

    run_p = sub.add_parser("run", help="run a task against an agent")
    run_p.add_argument("--agent", required=True)
    run_p.add_argument("--agents-dir", default="docs/agents")
    run_p.add_argument("--task", required=True, help="path to JSON file or '-' for stdin")
    run_p.add_argument("--thread-id", default=None)
    run_p.add_argument("--permission-mode", default=None, choices=["ask", "auto", "plan", "bypass"])

    resume_p = sub.add_parser("resume", help="resume an interrupted thread with a Command(resume=) payload")
    resume_p.add_argument("--agents-dir", default="docs/agents")
    resume_p.add_argument("--thread-id", required=True)
    resume_p.add_argument(
        "--payload",
        default="-",
        help="path to JSON file with the resume payload, or '-' for stdin (defaults to stdin)",
    )

    sub.add_parser("info", help="print version and config diagnostics")

    parsed = parser.parse_args(args)

    if parsed.cmd == "run":
        return _cmd_run(parsed)
    if parsed.cmd == "resume":
        return _cmd_resume(parsed)
    if parsed.cmd == "info":
        return _cmd_info()

    print(f"modi-harness {__version__}")
    print("Usage: modi run --agent NAME --task task.json [--thread-id T]")
    print("       modi resume --thread-id T [--payload payload.json]")
    print("       modi --version")
    return 0


def _cmd_info() -> int:
    print(f"modi-harness {__version__}")
    return 0


def _read_json(source: str) -> dict:
    if source == "-":
        return json.loads(sys.stdin.read())
    return json.loads(Path(source).read_text(encoding="utf-8"))


def _cmd_run(parsed) -> int:
    from . import ModiHarness

    task = _read_json(parsed.task)
    harness = ModiHarness(agents_dir=parsed.agents_dir)
    response = harness.run_task(
        agent=parsed.agent,
        input=task,
        permission_mode=parsed.permission_mode,
        thread_id=parsed.thread_id,
    )
    print(json.dumps(response, ensure_ascii=False, indent=2, default=str))
    return 0 if response["status"] == "completed" else 1


def _cmd_resume(parsed) -> int:
    from . import ModiHarness

    payload = _read_json(parsed.payload)
    harness = ModiHarness(agents_dir=parsed.agents_dir)
    response = harness.resume_task(thread_id=parsed.thread_id, payload=payload)
    print(json.dumps(response, ensure_ascii=False, indent=2, default=str))
    return 0 if response["status"] == "completed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
