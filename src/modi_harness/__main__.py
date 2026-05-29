"""CLI smoke entry. Lightweight V0.2 CLI for running a sample task."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import __version__
from .cli.runner import run_streaming


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
    stream_group = run_p.add_mutually_exclusive_group()
    stream_group.add_argument("--stream", action="store_true", default=None)
    stream_group.add_argument("--no-stream", action="store_true", default=None, dest="no_stream")

    resume_p = sub.add_parser("resume", help="resume an interrupted thread with a Command(resume=) payload")
    resume_p.add_argument("--agents-dir", default="docs/agents")
    resume_p.add_argument("--thread-id", required=True)
    resume_p.add_argument(
        "--payload",
        default="-",
        help="path to JSON file with the resume payload, or '-' for stdin (defaults to stdin)",
    )

    sub.add_parser("info", help="print version and config diagnostics")

    plugins_p = sub.add_parser("plugins", help="inspect installed plugins")
    plugins_sub = plugins_p.add_subparsers(dest="plugins_cmd")
    plugins_sub.add_parser("list", help="list discovered plugins and their contributions")

    parsed = parser.parse_args(args)

    if parsed.cmd == "run":
        return _cmd_run(parsed)
    if parsed.cmd == "resume":
        return _cmd_resume(parsed)
    if parsed.cmd == "info":
        return _cmd_info()
    if parsed.cmd == "plugins":
        if parsed.plugins_cmd == "list":
            return _cmd_plugins_list()
        plugins_p.print_help()
        return 0

    print(f"modi-harness {__version__}")
    print("Usage: modi run --agent NAME --task task.json [--thread-id T]")
    print("       modi resume --thread-id T [--payload payload.json]")
    print("       modi plugins list")
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

    if parsed.no_stream:
        use_stream = False
    elif parsed.stream:
        use_stream = True
    else:
        use_stream = sys.stdout.isatty()

    if use_stream:
        import asyncio

        from rich.console import Console

        console = Console()
        return asyncio.run(
            run_streaming(
                harness,
                agent=parsed.agent,
                input=task,
                thread_id=parsed.thread_id,
                permission_mode=parsed.permission_mode,
                console=console,
            )
        )

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


def _cmd_plugins_list() -> int:
    import pathlib

    from . import plugins as plugins_module
    from .plugins import PluginLoadError

    try:
        plugins = plugins_module.discover_plugins()
    except PluginLoadError as exc:
        print(f"Error loading plugin: {exc}", file=sys.stderr)
        return 1

    if not plugins:
        print("No plugins discovered.")
        print("Install plugins via: pip install <plugin-package>")
        print("See docs/plugins.md for the plugin author guide.")
        return 0

    total_agents = 0
    total_skills = 0
    total_tools = 0

    print("Discovered plugins:")
    for p in plugins:
        agent_names: list[str] = []
        if p.get("agents_dir"):
            agent_names = sorted(
                f.stem for f in pathlib.Path(p["agents_dir"]).glob("*.md")
            )

        skill_names: list[str] = []
        if p.get("skills_dir"):
            skill_names = sorted(
                d.name
                for d in pathlib.Path(p["skills_dir"]).iterdir()
                if d.is_dir() and (d / "SKILL.md").exists()
            )

        tool_names = [spec["name"] for spec, _ in p.get("tools", [])]

        total_agents += len(agent_names)
        total_skills += len(skill_names)
        total_tools += len(tool_names)

        print(f"  {p['name']} ({p['source']})")
        if agent_names:
            print(f"    agents: {len(agent_names)} ({', '.join(agent_names)})")
        if skill_names:
            print(f"    skills: {len(skill_names)} ({', '.join(skill_names)})")
        if tool_names:
            print(f"    tools:  {len(tool_names)} ({', '.join(tool_names)})")

    plugin_word = "plugin" if len(plugins) == 1 else "plugins"
    agent_word = "agent" if total_agents == 1 else "agents"
    skill_word = "skill" if total_skills == 1 else "skills"
    tool_word = "tool" if total_tools == 1 else "tools"
    print(
        f"\n({len(plugins)} {plugin_word}, "
        f"{total_agents} {agent_word}, "
        f"{total_skills} {skill_word}, "
        f"{total_tools} {tool_word})"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
