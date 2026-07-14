"""Hook dispatcher: run matching hooks for an event, return results."""

from __future__ import annotations

import importlib
import json
import os
import shlex
import subprocess
from collections.abc import Callable
from pathlib import Path
from typing import Any, Literal, cast

from .._utils import new_ulid
from ..types import HookResult, HookSpec
from .registry import HookRegistry

# Events for which a hook may return decision=redirect. Other events downgrade
# redirect to proceed.
_REDIRECT_ALLOWED_EVENTS: frozenset[str] = frozenset(
    {"user_prompt_submit", "pre_model_call", "post_model_call"}
)


class HookDispatcher:
    """Dispatches an event to all matching hooks. Returns aggregated HookResult list."""

    def __init__(
        self,
        *,
        registry: HookRegistry,
        project_root: Path | str,
        pass_env: list[str],
    ) -> None:
        self._registry = registry
        self._project_root = Path(project_root)
        self._pass_env = list(pass_env)
        self._python_cache: dict[str, Callable[[dict[str, Any]], Any]] = {}

    def dispatch(self, event: str, payload: dict[str, Any]) -> list[HookResult]:
        results: list[HookResult] = []
        for spec in self._registry.for_event(event):
            if not _matches(spec.get("matcher"), payload):
                continue
            result = self._run_one(spec, payload)
            results.append(result)
            if spec["blocking"] and result["decision"] == "block":
                break  # short-circuit: first block stops further blocking hooks
        return results

    # ------------------------------------------------------------------
    # internals
    # ------------------------------------------------------------------

    def _run_one(self, spec: HookSpec, payload: dict[str, Any]) -> HookResult:
        hook_id = new_ulid()
        cmd = spec["command"]
        try:
            if cmd.startswith("python:"):
                stdout, exit_code, duration = self._run_python(spec, payload)
            else:
                stdout, exit_code, duration = self._run_shell(spec, payload)
        except _HookFailure as failure:
            return _failure_result(spec, hook_id, failure)

        return _interpret_stdout(spec, hook_id, stdout, exit_code, duration)

    # --- python ---

    def _run_python(self, spec: HookSpec, payload: dict[str, Any]) -> tuple[str, int, int]:
        target = spec["command"][len("python:") :]
        fn = self._resolve_python(target)
        try:
            result = fn(payload)
        except Exception as exc:
            raise _HookFailure(reason=f"python hook raised: {exc}") from exc
        if isinstance(result, dict):
            return json.dumps(result), 0, 0
        return str(result), 0, 0

    def _resolve_python(self, target: str) -> Callable[[dict[str, Any]], Any]:
        if target in self._python_cache:
            return self._python_cache[target]
        module_name, _, attr = target.rpartition(".")
        if not module_name or not attr:
            raise _HookFailure(reason=f"invalid python target: {target}")
        try:
            module = importlib.import_module(module_name)
            fn = getattr(module, attr)
        except Exception as exc:
            raise _HookFailure(reason=f"could not import {target}: {exc}") from exc
        if not callable(fn):
            raise _HookFailure(reason=f"python hook is not callable: {target}")
        typed = cast(Callable[[dict[str, Any]], Any], fn)
        self._python_cache[target] = typed
        return typed

    # --- shell ---

    def _run_shell(self, spec: HookSpec, payload: dict[str, Any]) -> tuple[str, int, int]:
        env = self._build_env(payload, spec)
        stdin_data: bytes | None = None
        argv: list[str]

        if spec["pass_payload"] == "stdin":
            stdin_data = json.dumps(payload).encode("utf-8")
            argv = (
                shlex.split(spec["command"])
                if spec["command"].strip().startswith(("/", "./"))
                else []
            )
            shell = not bool(argv)
        elif spec["pass_payload"] == "argv":
            scalars = [f"--{k}={v}" for k, v in payload.items() if not isinstance(v, (dict, list))]
            argv = shlex.split(spec["command"]) + scalars
            shell = False
        else:  # env
            argv = []
            shell = True

        try:
            proc = subprocess.run(
                spec["command"] if shell else argv,
                shell=shell,
                cwd=str(self._project_root),
                env=env,
                input=stdin_data,
                capture_output=True,
                timeout=spec["timeout_seconds"],
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise _HookFailure(reason="hook timed out") from exc

        if proc.returncode != 0 and spec["on_failure"] == "block":
            raise _HookFailure(
                reason=f"shell hook exited {proc.returncode}: {proc.stderr.decode(errors='replace')[:200]}"
            )

        return proc.stdout.decode(errors="replace"), proc.returncode, 0

    def _build_env(self, payload: dict[str, Any], spec: HookSpec) -> dict[str, str]:
        env = {k: os.environ[k] for k in self._pass_env if k in os.environ}
        if spec["pass_payload"] == "env":
            for k, v in payload.items():
                if isinstance(v, (dict, list)):
                    continue
                env[f"MODI_HOOK_{k.upper()}"] = str(v)
        return env


# ----------------------------------------------------------------------
# matchers, interpretation
# ----------------------------------------------------------------------


def _matches(matcher: dict[str, Any] | None, payload: dict[str, Any]) -> bool:
    if not matcher:
        return True
    for key, expected in matcher.items():
        actual = _payload_field(payload, key)
        if isinstance(expected, list):
            if actual not in expected:
                return False
        elif actual != expected:
            return False
    return True


def _payload_field(payload: dict[str, Any], key: str) -> Any:
    # Tolerate matcher key 'tool' for payload key 'tool_name'.
    if key == "tool" and "tool_name" in payload:
        return payload["tool_name"]
    return payload.get(key)


def _interpret_stdout(
    spec: HookSpec,
    hook_id: str,
    stdout: str,
    exit_code: int,
    duration_ms: int,
) -> HookResult:
    text = stdout.strip()
    decision = "proceed"
    feedback: str | None = None
    redirect: dict[str, Any] | None = None
    if text:
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            feedback = text
        else:
            if isinstance(parsed, dict):
                decision = parsed.get("decision", "proceed")
                feedback = parsed.get("feedback")
                redirect = parsed.get("redirect")
            else:
                feedback = text

    if decision == "redirect" and spec["event"] not in _REDIRECT_ALLOWED_EVENTS:
        decision = "proceed"
        redirect = None

    return HookResult(
        event=spec["event"],
        hook_id=hook_id,
        decision=cast(Literal["proceed", "block", "redirect"], decision),
        feedback=feedback,
        redirect=redirect,
        exit_code=exit_code,
        duration_ms=duration_ms,
        stdout_ref=None,
        stderr_ref=None,
    )


def _failure_result(spec: HookSpec, hook_id: str, failure: _HookFailure) -> HookResult:
    decision: Literal["proceed", "block", "redirect"]
    if spec["on_failure"] == "block":
        decision = "block"
    elif spec["on_failure"] == "ignore":
        decision = "proceed"
    else:  # warn
        decision = "proceed"
    return HookResult(
        event=spec["event"],
        hook_id=hook_id,
        decision=decision,
        feedback=failure.reason,
        redirect=None,
        exit_code=-1,
        duration_ms=0,
        stdout_ref=None,
        stderr_ref=None,
    )


class _HookFailure(Exception):
    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason
