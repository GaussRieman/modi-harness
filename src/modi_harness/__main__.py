"""CLI smoke entry. Lightweight V0.2 CLI for running a sample task."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

from . import __version__
from .api.errors import AgentFactoryError, AgentResolutionError, ModiConfigError
from .cli.input import read_cli_input
from .cli.runner import run_streaming
from .discovery import AgentDescriptor, DiscoveryResult, discover_agents
from .models.errors import ModelConfigError

_RESERVED_COMMANDS = {"agents", "help", "info", "plugins", "resume", "run"}
_CLI_ERRORS = (AgentFactoryError, AgentResolutionError, ModiConfigError, ModelConfigError)

if TYPE_CHECKING:
    from .api import ModiSession


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv

    if args and args[0] in {"-V", "--version"}:
        print(__version__)
        return 0
    if args and not args[0].startswith("-") and args[0] not in _RESERVED_COMMANDS:
        try:
            return _cmd_dynamic_agent(args)
        except _CLI_ERRORS as exc:
            print(f"Error: {exc}", file=sys.stderr)
            return 2

    parser = argparse.ArgumentParser(prog="modi", description="Modi Harness CLI")
    sub = parser.add_subparsers(dest="cmd")

    run_p = sub.add_parser("run", help="run a task against an agent")
    run_p.add_argument("agent_name", nargs="?", help="unqualified or qualified Agent name")
    run_p.add_argument("--agent", dest="agent_option", help="legacy Agent name flag")
    run_p.add_argument(
        "--agents-dir",
        action="append",
        default=[],
        help="explicit Agent directory; repeatable",
    )
    run_p.add_argument("--task", help="path to JSON file or '-' for stdin")
    run_p.add_argument("--thread-id", default=None)
    run_p.add_argument("--permission-mode", default=None, choices=["auto", "preview", "trust"])
    run_p.add_argument(
        "--max-steps", type=int, default=None, help="maximum graph steps for this run"
    )
    stream_group = run_p.add_mutually_exclusive_group()
    stream_group.add_argument("--stream", action="store_true", default=None)
    stream_group.add_argument("--no-stream", action="store_true", default=None, dest="no_stream")
    run_p.add_argument(
        "--stream-format",
        choices=["live", "plain", "jsonl"],
        default=None,
        help="stream presentation; defaults to live on a TTY",
    )

    resume_p = sub.add_parser(
        "resume", help="resume an interrupted thread with a Command(resume=) payload"
    )
    resume_p.add_argument("--agents-dir", required=True, help="directory of agent .md files")
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

    agents_p = sub.add_parser("agents", help="discover and inspect Agents")
    agents_sub = agents_p.add_subparsers(dest="agents_cmd")
    agents_list = agents_sub.add_parser("list", help="list discovered Agents")
    agents_list.add_argument("--verbose", action="store_true")
    agents_show = agents_sub.add_parser("show", help="show one Agent")
    agents_show.add_argument("name")
    agents_which = agents_sub.add_parser("which", help="explain Agent resolution")
    agents_which.add_argument("name")
    agents_which.add_argument("--all", action="store_true", dest="show_all")
    for command in (agents_list, agents_show, agents_which):
        command.add_argument("--agents-dir", action="append", default=[])

    parsed = parser.parse_args(args)

    try:
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
        if parsed.cmd == "agents":
            if parsed.agents_cmd:
                return _cmd_agents(parsed)
            agents_p.print_help()
            return 0
    except _CLI_ERRORS as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2

    print(f"modi-harness {__version__}")
    print("Usage: modi AGENT [message]")
    print("       modi agents list|show|which")
    print("       modi run NAME --task task.json  # automation")
    print("       modi resume --thread-id T [--payload payload.json]")
    print("       modi plugins list")
    print("       modi --version")
    return 0


def _cmd_info() -> int:
    print(f"modi-harness {__version__}")
    return 0


def _read_json(source: str) -> dict[str, Any]:
    if source == "-":
        return cast(dict[str, Any], json.loads(sys.stdin.read()))
    return cast(dict[str, Any], json.loads(Path(source).read_text(encoding="utf-8")))


def _build_session(parsed: argparse.Namespace) -> ModiSession:
    """Construct a ModiSession for the CLI from parsed args.

    Builds a chat model from env via create_chat_model, a capability-only
    ModiHarness, loads agents from --agents-dir, and binds an in-memory
    checkpointer. Tests patch this function to inject a scripted session.
    """
    from langgraph.checkpoint.memory import MemorySaver

    from . import ModiHarness, ModiSession
    from .config import Settings
    from .models.factory import create_chat_model

    result = _discover_for_args(parsed)
    if getattr(parsed, "cmd", None) == "resume":
        agents = [descriptor.agent for descriptor in result.registry.list()]
        if not agents:
            raise AgentResolutionError("", (), detail="no Agents available to resume thread")
    else:
        descriptor = result.registry.resolve(_agent_query(parsed))
        parsed.resolved_agent_name = descriptor.agent.name
        agents = [descriptor.agent]
    settings = Settings(_env_file=result.project_root / ".env")
    chat_model = create_chat_model(
        provider=settings.model.provider,
        name=settings.model.name,
        api_key=settings.model.api_key,
        base_url=settings.model.base_url,
        timeout=settings.model.timeout,
    )
    harness = ModiHarness(chat_model=chat_model, kernel_tools=list(result.kernel_tools))
    return ModiSession(
        harness=harness,
        agents=agents,
        checkpointer=MemorySaver(),
        workspace_root=result.project_root / ".modi" / "workspace",
        memory_root=result.project_root / ".modi" / "memory",
        project_root=result.project_root,
        max_steps=getattr(parsed, "max_steps", None) or settings.runtime.max_steps,
    )


def _cmd_run(parsed: argparse.Namespace) -> int:
    task = _task_input(parsed)
    if task is None:
        return 2
    return _execute_task(parsed, task)


def _cmd_dynamic_agent(argv: list[str]) -> int:
    query = argv[0]
    parser = argparse.ArgumentParser(prog=f"modi {query}")
    parser.add_argument("message", nargs="*")
    parser.add_argument("--thread-id", default=None)
    parser.add_argument(
        "--permission-mode",
        default=None,
        choices=["auto", "preview", "trust"],
    )
    parser.add_argument("--stream-format", choices=["live", "plain", "jsonl"], default=None)
    parser.add_argument("--max-steps", type=int, default=None)
    parsed = parser.parse_args(argv[1:])
    parsed.cmd = "dynamic"
    parsed.agent_name = query
    parsed.agent_option = None
    parsed.agents_dir = []
    parsed.stream = None
    parsed.no_stream = None

    discovery = _discover_for_args(parsed)
    descriptor = discovery.registry.resolve(query)
    message = " ".join(parsed.message).strip()
    task: dict[str, Any]
    if message:
        task = {"prompt": message}
    elif descriptor.agent.interaction_protocol.startup == "agent":
        if not (sys.stdin.isatty() and sys.stdout.isatty()):
            print(
                "Error: interactive Agent startup requires a TTY; use the API or "
                "'modi run NAME --task -' for automation",
                file=sys.stderr,
            )
            return 2
        task = {"interactive_startup": True}
    else:
        if not (sys.stdin.isatty() and sys.stdout.isatty()):
            print("Error: an initial message is required outside a TTY", file=sys.stderr)
            return 2
        try:
            message = read_cli_input(f"Message for {descriptor.name}\n> ").strip()
        except (EOFError, KeyboardInterrupt):
            print(file=sys.stderr)
            return 1
        if not message:
            print("Error: message cannot be empty", file=sys.stderr)
            return 2
        task = {"prompt": message}
    return _execute_task(parsed, task)


def _execute_task(parsed: argparse.Namespace, task: dict[str, Any]) -> int:
    query = _agent_query(parsed)
    session = _build_session(parsed)
    run_agent = getattr(parsed, "resolved_agent_name", query)

    stream_format = getattr(parsed, "stream_format", None)
    if stream_format is not None:
        use_stream = True
    elif getattr(parsed, "no_stream", None):
        use_stream = False
    elif getattr(parsed, "stream", None):
        use_stream = True
    else:
        use_stream = sys.stdout.isatty()

    if use_stream:
        import asyncio

        from rich.console import Console

        from .cli.renderer import JsonlRenderer, TaskProgressRenderer

        stream_format = stream_format or "live"
        console = Console(no_color=stream_format != "live")
        renderer = (
            JsonlRenderer(console) if stream_format == "jsonl" else TaskProgressRenderer(console)
        )

        return asyncio.run(
            run_streaming(
                session,
                agent=run_agent,
                input=task,
                thread_id=parsed.thread_id,
                permission_mode=parsed.permission_mode,
                console=console,
                renderer=renderer,
            )
        )

    response = session.run_task(
        agent=run_agent,
        input=task,
        mode=parsed.permission_mode,
        thread_id=parsed.thread_id,
    )
    print(json.dumps(response, ensure_ascii=False, indent=2, default=str))
    return 0 if response["status"] == "completed" else 1


def _cmd_resume(parsed: argparse.Namespace) -> int:
    payload = _read_json(parsed.payload)
    session = _build_session(parsed)
    response = session.resume_task(thread_id=parsed.thread_id, payload=payload)
    print(json.dumps(response, ensure_ascii=False, indent=2, default=str))
    return 0 if response["status"] == "completed" else 1


def _agent_query(parsed: argparse.Namespace) -> str:
    positional = getattr(parsed, "agent_name", None)
    option = getattr(parsed, "agent_option", None)
    legacy = getattr(parsed, "agent", None)
    if positional and option:
        raise AgentResolutionError(
            str(positional), (), detail="positional Agent and --agent are mutually exclusive"
        )
    query = positional or option or legacy
    if not query:
        raise AgentResolutionError("", (), detail="an Agent name is required")
    return str(query)


def _task_input(parsed: argparse.Namespace) -> dict[str, Any] | None:
    source = getattr(parsed, "task", None)
    if source:
        return _read_json(source)
    if not (sys.stdin.isatty() and sys.stdout.isatty()):
        print("Error: --task is required when stdin/stdout are not interactive", file=sys.stderr)
        return None
    try:
        prompt = read_cli_input(f"Task for {_agent_query(parsed)}\n> ").strip()
    except (EOFError, KeyboardInterrupt):
        print(file=sys.stderr)
        return None
    if not prompt:
        print("Error: task cannot be empty", file=sys.stderr)
        return None
    return {"prompt": prompt}


def _discover_for_args(parsed: argparse.Namespace) -> DiscoveryResult:
    raw_dirs = getattr(parsed, "agents_dir", []) or []
    if isinstance(raw_dirs, (str, Path)):
        raw_dirs = [raw_dirs]
    return discover_agents(cwd=Path.cwd(), explicit_dirs=raw_dirs)


def _cmd_agents(parsed: argparse.Namespace) -> int:
    result = _discover_for_args(parsed)
    registry = result.registry
    if parsed.agents_cmd == "list":
        descriptors = registry.list()
        if not descriptors:
            print("No Agents discovered.")
            return 0
        for descriptor in descriptors:
            if parsed.verbose:
                print(_descriptor_details(descriptor))
            else:
                print(f"{descriptor.name}\t{descriptor.qualified_name}")
        for notice in result.notices:
            print(f"notice: {notice}", file=sys.stderr)
        return 0

    report = registry.explain(parsed.name)
    if parsed.agents_cmd == "which" and parsed.show_all:
        if not report.candidates:
            registry.resolve(parsed.name)
        for descriptor in report.candidates:
            marker = "*" if descriptor == report.selected else "-"
            print(f"{marker} {descriptor.qualified_name}\t{_source_location(descriptor)}")
        return 0

    descriptor = registry.resolve(parsed.name)
    if parsed.agents_cmd == "which":
        print(descriptor.qualified_name)
        print(_source_location(descriptor))
        return 0
    if parsed.agents_cmd == "show":
        print(_descriptor_details(descriptor))
        print(f"description: {descriptor.agent.description}")
        print(
            f"tools: {', '.join(binding.spec['name'] for binding in descriptor.agent.tools) or '-'}"
        )
        print(f"skills: {', '.join(skill.name for skill in descriptor.agent.skills) or '-'}")
        protocol = descriptor.agent.task_protocol
        print(f"task protocol: {protocol.mode} (review: {protocol.review})")
        print(f"interactive startup: {descriptor.agent.interaction_protocol.startup}")
        return 0
    return 2


def _descriptor_details(descriptor: AgentDescriptor) -> str:
    lines = [
        descriptor.name,
        f"  qualified: {descriptor.qualified_name}",
        f"  source: {descriptor.source_kind}",
        f"  location: {_source_location(descriptor)}",
        f"  factory: {'yes' if descriptor.executable_factory else 'no'}",
    ]
    return "\n".join(lines)


def _source_location(descriptor: AgentDescriptor) -> str:
    if descriptor.path is not None:
        return str(descriptor.path)
    return descriptor.source_id


def _cmd_plugins_list() -> int:
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
        print("See docs/guides/plugins.md for the plugin author guide.")
        return 0

    total_agents = 0
    total_tools = 0
    print("Discovered plugins:")
    for p in plugins:
        agents = p.get("agents", [])
        kernel_tools = p.get("kernel_tools", [])
        agent_names = sorted(a.name for a in agents)
        tool_names = [t.spec["name"] for t in kernel_tools]
        total_agents += len(agent_names)
        total_tools += len(tool_names)
        print(f"  {p['name']} ({p['source']})")
        if agent_names:
            print(f"    agents: {len(agent_names)} ({', '.join(agent_names)})")
        if tool_names:
            print(f"    kernel_tools: {len(tool_names)} ({', '.join(tool_names)})")

    plugin_word = "plugin" if len(plugins) == 1 else "plugins"
    agent_word = "agent" if total_agents == 1 else "agents"
    tool_word = "tool" if total_tools == 1 else "tools"
    print(
        f"\n({len(plugins)} {plugin_word}, {total_agents} {agent_word}, {total_tools} {tool_word})"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
