from __future__ import annotations

import inspect
from typing import Any, Callable, cast
from unittest.mock import MagicMock

import pytest

from agent.image_gen_provider import ImageGenProvider
from agent.image_gen_registry import get_provider as get_image_provider
from agent.image_gen_registry import register_provider as register_image_provider
from agent.secret_sources.base import SecretSource
from agent.secret_sources import registry as secret_registry
from gateway.platform_registry import PlatformEntry, platform_registry
from hermes_cli.plugins import (
    LoadedPlugin,
    PluginContext,
    PluginManager,
    PluginManifest,
)
from tools.registry import registry as tool_registry


def _manifest(name: str = "owner-plugin") -> PluginManifest:
    return PluginManifest(
        name=name,
        version="1.0.0",
        description="owner ledger test",
        source="bundled",
        kind="standalone",
        key=name,
    )


def _tool_handler(label: str):
    def _handler(**_kwargs):
        return label

    return _handler


def _register_tool_raw(name: str, handler, *, toolset: str = "runtime") -> None:
    tool_registry.register(
        name=name,
        toolset=toolset,
        schema={"name": name, "description": name, "parameters": {"type": "object"}},
        handler=handler,
        override=True,
    )


def _plugin_register_tool(ctx: PluginContext, name: str, handler) -> None:
    ctx.register_tool(
        name=name,
        toolset="plugin-tools",
        schema={"name": name, "description": name, "parameters": {"type": "object"}},
        handler=handler,
        override=True,
    )


def _platform_entry(name: str, label: str) -> PlatformEntry:
    return PlatformEntry(
        name=name,
        label=label,
        adapter_factory=lambda _cfg: label,
        check_fn=lambda: True,
        source="builtin",
    )


def test_every_plugin_context_registrar_records_owner_ledger_metadata():
    registrars = {
        name
        for name, value in vars(PluginContext).items()
        if name.startswith("register_") and callable(value)
    }
    expected = {
        "register_tool",
        "register_cli_command",
        "register_command",
        "register_context_engine",
        "register_image_gen_provider",
        "register_dashboard_auth_provider",
        "register_video_gen_provider",
        "register_web_search_provider",
        "register_browser_provider",
        "register_secret_source",
        "register_tts_provider",
        "register_transcription_provider",
        "register_platform",
        "register_slack_action_handler",
        "register_auxiliary_task",
        "register_turn_gate_provider",
        "register_hook",
        "register_middleware",
        "register_skill",
    }
    assert registrars == expected
    for name in sorted(registrars):
        source = inspect.getsource(getattr(PluginContext, name))
        assert (
            "_record_owned_registration" in source
            or "_record_mapping_registration" in source
            or "_record_registry_slot" in source
        ), name


@pytest.fixture(autouse=True)
def _restore_global_host_state():
    snapshot = PluginManager._snapshot_force_reload_host_state()
    try:
        yield
    finally:
        PluginManager._restore_force_reload_host_state(snapshot)


def test_successful_force_reload_removes_only_prior_owner_entries(monkeypatch):
    manager = PluginManager()
    manifest = _manifest()
    ctx = PluginContext(manifest, manager)

    builtin_handler = _tool_handler("builtin")
    runtime_handler = _tool_handler("runtime")
    old_plugin_handler = _tool_handler("old-plugin")
    _register_tool_raw("owned-tool", builtin_handler, toolset="builtin-tools")
    _register_tool_raw("runtime-mcp-tool", runtime_handler, toolset="mcp-live")
    generation_before_plugin = tool_registry._generation

    runtime_hook = lambda **_kwargs: "runtime"  # noqa: E731
    old_plugin_hook = lambda **_kwargs: "old"  # noqa: E731
    candidate_hook = lambda **_kwargs: "candidate"  # noqa: E731
    manager._hooks["pre_llm_call"] = [runtime_hook]
    _plugin_register_tool(ctx, "owned-tool", old_plugin_handler)
    ctx.register_hook("pre_llm_call", old_plugin_hook)
    manager._plugins[manifest.key] = LoadedPlugin(manifest=manifest, enabled=True)
    manager._discovered = True

    ledger_reader = getattr(manager, "get_owner_ledger", None)
    assert callable(ledger_reader)
    read_ledger = cast(Callable[[], list[dict[str, Any]]], ledger_reader)
    assert {item["surface"] for item in read_ledger()} >= {"tool", "hook"}

    def _candidate_round() -> None:
        PluginContext(manifest, manager).register_hook("pre_llm_call", candidate_hook)

    monkeypatch.setattr(manager, "_discover_and_load_inner", _candidate_round)
    manager.discover_and_load(force=True)

    owned_entry = tool_registry.get_entry("owned-tool")
    runtime_entry = tool_registry.get_entry("runtime-mcp-tool")
    assert owned_entry is not None
    assert runtime_entry is not None
    assert owned_entry.handler is builtin_handler
    assert runtime_entry.handler is runtime_handler
    assert manager._hooks["pre_llm_call"] == [runtime_hook, candidate_hook]
    assert tool_registry._generation > generation_before_plugin
    assert all(item["owner_id"] == manifest.key for item in read_ledger())
    assert {item["surface"] for item in read_ledger()} == {"hook"}


def test_failed_force_reload_restores_exact_registry_objects_and_generation(
    monkeypatch,
):
    manager = PluginManager()
    manifest = _manifest()
    ctx = PluginContext(manifest, manager)
    builtin_handler = _tool_handler("builtin")
    old_plugin_handler = _tool_handler("old-plugin")
    candidate_handler = _tool_handler("candidate")
    old_hook = lambda **_kwargs: "old"  # noqa: E731

    _register_tool_raw("owned-tool", builtin_handler, toolset="builtin-tools")
    _plugin_register_tool(ctx, "owned-tool", old_plugin_handler)
    ctx.register_hook("post_tool_call", old_hook)
    manager._plugins[manifest.key] = LoadedPlugin(manifest=manifest, enabled=True)
    manager._discovered = True
    exact_entry_before = tool_registry.get_entry("owned-tool")
    exact_hooks_before = list(manager._hooks["post_tool_call"])
    exact_generation_before = tool_registry._generation
    exact_ledger_before = getattr(manager, "get_owner_ledger")()

    def _failing_round() -> None:
        candidate_ctx = PluginContext(manifest, manager)
        _plugin_register_tool(candidate_ctx, "candidate-only-tool", candidate_handler)
        candidate_ctx.register_hook("post_tool_call", lambda **_kwargs: "candidate")
        raise RuntimeError("candidate reload failed")

    monkeypatch.setattr(manager, "_discover_and_load_inner", _failing_round)
    with pytest.raises(RuntimeError, match="candidate reload failed"):
        manager.discover_and_load(force=True)

    assert tool_registry.get_entry("owned-tool") is exact_entry_before
    assert tool_registry.get_entry("candidate-only-tool") is None
    assert manager._hooks["post_tool_call"] == exact_hooks_before
    assert tool_registry._generation == exact_generation_before
    assert getattr(manager, "get_owner_ledger")() == exact_ledger_before
    assert manager._discovered is True


def test_successful_reload_restores_overridden_host_objects_and_removes_stale_providers(
    monkeypatch,
):
    manager = PluginManager()
    manifest = _manifest()
    ctx = PluginContext(manifest, manager)

    builtin_platform = _platform_entry("owned-platform", "builtin")
    platform_registry.register(builtin_platform)
    ctx.register_platform(
        name="owned-platform",
        label="plugin",
        adapter_factory=lambda _cfg: "plugin",
        check_fn=lambda: True,
    )

    builtin_image = MagicMock(spec=ImageGenProvider)
    builtin_image.name = "owned-image"
    plugin_image = MagicMock(spec=ImageGenProvider)
    plugin_image.name = "owned-image"
    register_image_provider(builtin_image)
    ctx.register_image_gen_provider(plugin_image)

    plugin_secret = MagicMock(spec=SecretSource)
    plugin_secret.name = "owner_secret"
    plugin_secret.api_version = 1
    plugin_secret.shape = "mapped"
    plugin_secret.scheme = "owner"
    ctx.register_secret_source(plugin_secret)

    manager._plugins[manifest.key] = LoadedPlugin(manifest=manifest, enabled=True)
    manager._discovered = True
    monkeypatch.setattr(manager, "_discover_and_load_inner", lambda: None)
    manager.discover_and_load(force=True)

    assert platform_registry.get("owned-platform") is builtin_platform
    assert get_image_provider("owned-image") is builtin_image
    assert secret_registry._SOURCES.get("owner_secret") is None
    assert getattr(manager, "get_owner_ledger")() == []
