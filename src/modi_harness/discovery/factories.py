"""Trusted project Agent package factory loading."""

from __future__ import annotations

import hashlib
import importlib.util
import sys
import tomllib
from pathlib import Path
from types import ModuleType

from ..api.agent import ModiAgent
from ..api.errors import AgentFactoryError

_MANIFEST_KEYS = {"factory"}


def load_agent_package(package_dir: Path) -> ModiAgent:
    """Load one trusted ``agent.toml`` package factory."""
    package_dir = package_dir.resolve()
    manifest_path = package_dir / "agent.toml"
    try:
        with manifest_path.open("rb") as handle:
            manifest = tomllib.load(handle)
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise AgentFactoryError(package_dir, f"cannot read agent.toml: {exc}") from exc

    unknown = sorted(set(manifest) - _MANIFEST_KEYS)
    if unknown:
        raise AgentFactoryError(package_dir, f"unknown agent.toml key(s): {', '.join(unknown)}")
    target = manifest.get("factory")
    if not isinstance(target, str) or ":" not in target:
        raise AgentFactoryError(package_dir, "factory must use 'module:function' syntax")
    module_ref, function_name = target.rsplit(":", 1)
    if not module_ref or not function_name:
        raise AgentFactoryError(package_dir, "factory must use 'module:function' syntax")

    module = _load_package_module(package_dir, module_ref)
    factory = getattr(module, function_name, None)
    if not callable(factory):
        raise AgentFactoryError(package_dir, f"factory target is not callable: {target}")
    try:
        agent = factory()
    except Exception as exc:
        raise AgentFactoryError(
            package_dir, f"factory {target} raised {type(exc).__name__}: {exc}"
        ) from exc
    if not isinstance(agent, ModiAgent):
        raise AgentFactoryError(
            package_dir,
            f"factory {target} returned {type(agent).__name__}, expected ModiAgent",
        )
    return agent


def _load_package_module(package_dir: Path, module_ref: str) -> ModuleType:
    relative = Path(*module_ref.split("."))
    module_path = package_dir / relative.with_suffix(".py")
    if not module_path.is_file():
        package_init = package_dir / relative / "__init__.py"
        if package_init.is_file():
            module_path = package_init
        else:
            raise AgentFactoryError(package_dir, f"factory module not found: {module_ref}")

    digest = hashlib.sha256(str(package_dir).encode("utf-8")).hexdigest()[:16]
    package_name = f"_modi_project_agent_{digest}"
    if package_name not in sys.modules:
        package = ModuleType(package_name)
        package.__path__ = [str(package_dir)]
        package.__package__ = package_name
        sys.modules[package_name] = package
    full_name = f"{package_name}.{module_ref}"
    existing = sys.modules.get(full_name)
    if existing is not None:
        return existing
    spec = importlib.util.spec_from_file_location(full_name, module_path)
    if spec is None or spec.loader is None:
        raise AgentFactoryError(package_dir, f"cannot load factory module: {module_ref}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[full_name] = module
    try:
        spec.loader.exec_module(module)
    except Exception as exc:
        sys.modules.pop(full_name, None)
        raise AgentFactoryError(
            package_dir, f"cannot import factory module {module_ref}: {exc}"
        ) from exc
    return module


__all__ = ["load_agent_package"]
