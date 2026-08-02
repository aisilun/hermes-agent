from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

import agent.agent_runtime_helpers as runtime_helpers
import agent.tool_executor as tool_executor
import hermes_cli.plugins as plugins
import model_tools


TOOL_NAME = "read_file"
SECRET = "TOP_SECRET_RESOLVER_DETAIL"
EXPECTED_BLOCK = f"BLOCKED: plugin pre-tool authorization failed for {TOOL_NAME}"


def _raise_authorization_error(*_args, **_kwargs):
    raise RuntimeError(SECRET)


def _direct_tool_middleware(monkeypatch) -> None:
    monkeypatch.setattr(
        "hermes_cli.middleware.apply_tool_request_middleware",
        lambda _name, args, **_kwargs: SimpleNamespace(
            payload=args,
            original_payload=args,
            trace=[],
        ),
    )
    monkeypatch.setattr(
        "hermes_cli.middleware.run_tool_execution_middleware",
        lambda _name, args, callback, **_kwargs: callback(args),
    )
    monkeypatch.setattr(
        "agent.relay_tools.execute",
        lambda _name, args, callback, **_kwargs: (callback(args), args),
    )


def test_resolver_fails_closed_when_pre_tool_hook_callback_raises(monkeypatch):
    manager = plugins.PluginManager()
    callback = MagicMock(side_effect=RuntimeError(SECRET))
    manager._hooks["pre_tool_call"] = [callback]
    monkeypatch.setattr(plugins, "_plugin_manager", manager)

    result = plugins.resolve_pre_tool_block(TOOL_NAME, {"path": SECRET})

    assert result == EXPECTED_BLOCK
    assert result is not None
    assert SECRET not in result
    callback.assert_called_once()


def test_registry_dispatch_blocks_resolver_exception_and_emits_audit(monkeypatch):
    entry = model_tools.registry.get_entry(TOOL_NAME)
    assert entry is not None
    handler = MagicMock(return_value=json.dumps({"leaked": SECRET}))
    audits = []
    monkeypatch.setattr(entry, "handler", handler)
    monkeypatch.setattr(plugins, "resolve_pre_tool_block", _raise_authorization_error)
    monkeypatch.setattr(
        model_tools,
        "_emit_post_tool_call_hook",
        lambda **kwargs: audits.append(kwargs),
    )

    result = model_tools.handle_function_call(
        TOOL_NAME,
        {"path": SECRET},
        task_id="task-1",
        session_id="session-1",
        tool_call_id="call-1",
        skip_tool_request_middleware=True,
    )

    handler.assert_not_called()
    assert EXPECTED_BLOCK in result
    assert SECRET not in result
    assert len(audits) == 1
    assert audits[0]["status"] == "blocked"
    assert audits[0]["error_type"] == "plugin_authorization_error"
    assert audits[0]["error_message"] == EXPECTED_BLOCK


def test_inline_agent_tool_blocks_resolver_exception_and_emits_audit(monkeypatch):
    _direct_tool_middleware(monkeypatch)
    handler = MagicMock(return_value=json.dumps({"leaked": SECRET}))
    audits = []
    monkeypatch.setattr("tools.todo_tool.todo_tool", handler)
    monkeypatch.setattr(plugins, "resolve_pre_tool_block", _raise_authorization_error)
    monkeypatch.setattr(
        model_tools,
        "_emit_post_tool_call_hook",
        lambda **kwargs: audits.append(kwargs),
    )
    agent = SimpleNamespace(
        session_id="session-1",
        _current_turn_id="turn-1",
        _current_api_request_id="api-1",
        _todo_store=object(),
    )

    result = runtime_helpers.invoke_tool(
        agent,
        "todo",
        {"todos": [{"content": SECRET}]},
        "task-1",
        tool_call_id="call-1",
    )

    handler.assert_not_called()
    assert "BLOCKED: plugin pre-tool authorization failed for todo" in result
    assert SECRET not in result
    assert len(audits) == 1
    assert audits[0]["status"] == "blocked"
    assert audits[0]["error_type"] == "plugin_authorization_error"


@pytest.mark.parametrize("concurrent", [False, True], ids=["sequential", "parallel-segmented"])
def test_tool_executor_blocks_resolver_exception_before_handler(
    monkeypatch,
    concurrent: bool,
):
    _direct_tool_middleware(monkeypatch)
    execute = MagicMock(return_value=json.dumps({"leaked": SECRET}))
    audits = []
    monkeypatch.setattr(plugins, "resolve_pre_tool_block", _raise_authorization_error)
    monkeypatch.setattr(
        tool_executor,
        "_emit_terminal_post_tool_call",
        lambda _agent, **kwargs: audits.append(kwargs),
    )
    authorization_gate = (
        tool_executor._ConcurrentToolAuthorizationGate() if concurrent else None
    )
    agent = SimpleNamespace(
        session_id="session-1",
        _current_turn_id="turn-1",
        _current_api_request_id="api-1",
    )

    outcome = tool_executor._run_agent_tool_execution_middleware(
        agent,
        function_name=TOOL_NAME,
        function_args={"path": SECRET},
        effective_task_id="task-1",
        tool_call_id="call-1",
        execute=execute,
        authorization_gate=authorization_gate,
    )

    execute.assert_not_called()
    assert outcome.blocked is True
    assert EXPECTED_BLOCK in outcome.result
    assert SECRET not in outcome.result
    assert len(audits) == 1
    assert audits[0]["status"] == "blocked"
    assert audits[0]["error_type"] == "plugin_authorization_error"
    assert audits[0]["error_message"] == EXPECTED_BLOCK


def test_normal_registry_path_invokes_pre_tool_hook_once(monkeypatch):
    manager = plugins.PluginManager()
    callback = MagicMock(return_value=None)
    manager._hooks["pre_tool_call"] = [callback]
    monkeypatch.setattr(plugins, "_plugin_manager", manager)
    entry = model_tools.registry.get_entry(TOOL_NAME)
    assert entry is not None
    handler = MagicMock(return_value=json.dumps({"ok": True}))
    monkeypatch.setattr(entry, "handler", handler)

    result = model_tools.handle_function_call(
        TOOL_NAME,
        {"path": "/tmp/safe"},
        task_id="task-1",
        session_id="session-1",
        tool_call_id="call-1",
        skip_tool_request_middleware=True,
    )

    assert json.loads(result) == {"ok": True}
    callback.assert_called_once()
    handler.assert_called_once()
