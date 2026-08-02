from __future__ import annotations

from typing import Any, Callable, cast
from unittest.mock import AsyncMock, MagicMock

import pytest

from agent.turn_gate import (
    GateDecision,
    GateState,
    TurnGateRequest,
    acquire_outer_turn,
    build_runtime_identity,
    clear_turn_gate_registry_for_testing,
    configure_turn_gate_from_config,
    register_turn_gate_provider,
)
from gateway.config import PlatformConfig
import gateway.platforms.base as platform_base


class _SequencedProvider:
    def __init__(self, *states: GateState) -> None:
        self.states = list(states)
        self.requests = []
        self.released = []

    def acquire(self, request: TurnGateRequest) -> GateDecision:
        self.requests.append(request)
        state = self.states.pop(0)
        return GateDecision(
            provider_id="gate",
            state=state,
            lease_id=f"lease-{len(self.requests)}",
            generation=len(self.requests),
        )

    def validate(self, decision: GateDecision, checkpoint: str) -> GateDecision:
        del checkpoint
        return decision

    def release(self, decision: GateDecision) -> None:
        self.released.append(decision)


@pytest.fixture(autouse=True)
def _clear_turn_gate_registry():
    clear_turn_gate_registry_for_testing()
    yield
    clear_turn_gate_registry_for_testing()


def _configure(provider: _SequencedProvider) -> None:
    register_turn_gate_provider("gate", provider, owner_id="gate")
    configure_turn_gate_from_config(
        {
            "agent": {
                "turn_gate": {
                    "required_provider": "gate",
                    "runtime_identity": {"machine_id": "machine-a"},
                }
            }
        }
    )


def _outer_request() -> TurnGateRequest:
    identity = build_runtime_identity(
        surface="test-parent",
        session_scope="session-parent",
        turn_id="turn-parent",
    )
    return TurnGateRequest(
        entrypoint="test-parent",
        purpose="business",
        task_id="session-parent",
        identity=identity,
    )


def _matrix_adapter():
    from plugins.platforms.matrix.adapter import MatrixAdapter

    return MatrixAdapter(
        PlatformConfig(
            enabled=True,
            token="syt_test",
            extra={
                "homeserver": "https://matrix.example.org",
                "user_id": "@bot:example.org",
            },
        )
    )


def _whatsapp_adapter():
    from plugins.platforms.whatsapp.adapter import WhatsAppAdapter

    return WhatsAppAdapter(
        PlatformConfig(enabled=True, extra={"send_read_receipts": True})
    )


def test_inventory_explicitly_declares_private_write_sites_and_exemptions():
    import plugins.platforms.matrix.adapter as matrix_module
    import plugins.platforms.whatsapp.adapter as whatsapp_module

    MatrixAdapter = matrix_module.MatrixAdapter
    WhatsAppAdapter = whatsapp_module.WhatsAppAdapter

    inventory_reader = getattr(platform_base, "get_platform_mutation_inventory", None)
    exemption_reader = getattr(platform_base, "get_platform_mutation_exemptions", None)
    assert callable(inventory_reader)
    assert callable(exemption_reader)

    inventory = cast(Callable[[], dict[str, dict[str, Any]]], inventory_reader)()
    matrix_methods = {
        "send",
        "send_typing",
        "stop_typing",
        "edit_message",
        "_upload_and_send",
        "_join_room_by_id",
        "_send_reaction",
        "send_read_receipt",
        "redact_message",
        "create_room",
        "invite_user",
        "set_presence",
        "_send_simple_message",
        "_record_dm_room",
    }
    whatsapp_methods = {
        "send",
        "edit_message",
        "_send_media_to_bridge",
        "send_poll",
        "send_location",
        "send_typing",
        "_send_read_receipt",
    }
    expected_explicit = {
        *(f"{MatrixAdapter.__module__}.{MatrixAdapter.__qualname__}.{name}" for name in matrix_methods),
        *(f"{WhatsAppAdapter.__module__}.{WhatsAppAdapter.__qualname__}.{name}" for name in whatsapp_methods),
        f"{matrix_module.__name__}._standalone_send",
        f"{whatsapp_module.__name__}._standalone_send",
    }
    assert expected_explicit <= set(inventory)
    assert all(inventory[key]["declaration"] == "explicit" for key in expected_explicit)
    assert all(inventory[key]["kind"] == "platform_mutation" for key in expected_explicit)

    exemptions = cast(Callable[[], dict[str, dict[str, str]]], exemption_reader)()
    assert exemptions["BasePlatformAdapter.connect"]["reason"]
    assert exemptions["BasePlatformAdapter.disconnect"]["reason"]


@pytest.mark.asyncio
async def test_matrix_join_leave_and_dm_write_are_zero_without_required_provider():
    configure_turn_gate_from_config(
        {
            "agent": {
                "turn_gate": {
                    "required_provider": "missing-provider",
                    "runtime_identity": {"machine_id": "machine-a"},
                }
            }
        }
    )
    adapter = _matrix_adapter()
    adapter._joined_rooms = set()
    adapter._client = MagicMock()
    adapter._client.join_room = AsyncMock(side_effect=RuntimeError("room not found"))
    adapter._client.leave_room = AsyncMock()
    adapter._client.get_account_data = AsyncMock(return_value={})
    adapter._client.set_account_data = AsyncMock()

    with pytest.raises(RuntimeError, match="required provider"):
        await adapter._join_room_by_id("!room:example.org")
    with pytest.raises(RuntimeError, match="required provider"):
        await adapter._record_dm_room("!room:example.org", "@alice:example.org")

    adapter._client.join_room.assert_not_awaited()
    adapter._client.leave_room.assert_not_awaited()
    adapter._client.set_account_data.assert_not_awaited()


@pytest.mark.asyncio
async def test_matrix_detached_invite_does_not_inherit_parent_lease():
    provider = _SequencedProvider(GateState.OPEN, GateState.CLOSED_DRAINING)
    _configure(provider)
    adapter = _matrix_adapter()
    adapter._joined_rooms = set()
    adapter._client = MagicMock()
    adapter._client.join_room = AsyncMock(return_value=True)
    adapter._client.leave_room = AsyncMock()
    adapter._client.get_account_data = AsyncMock(return_value={})
    adapter._client.set_account_data = AsyncMock()

    with acquire_outer_turn(_outer_request()):
        adapter._schedule_invite_join(
            "!room:example.org",
            is_direct=True,
            inviter="@alice:example.org",
        )
        task = adapter._invite_join_tasks["!room:example.org"]
        await task

    assert len(provider.requests) == 2
    assert provider.requests[1].entrypoint == "matrix-invite-join"
    adapter._client.join_room.assert_not_awaited()
    adapter._client.leave_room.assert_not_awaited()
    adapter._client.set_account_data.assert_not_awaited()
    assert len(provider.released) == 2


@pytest.mark.asyncio
async def test_whatsapp_private_read_receipt_is_zero_without_required_provider():
    configure_turn_gate_from_config(
        {
            "agent": {
                "turn_gate": {
                    "required_provider": "missing-provider",
                    "runtime_identity": {"machine_id": "machine-a"},
                }
            }
        }
    )
    adapter = _whatsapp_adapter()
    session = MagicMock()
    session.post = MagicMock()
    adapter._http_session = session

    with pytest.raises(RuntimeError, match="required provider"):
        await adapter._send_read_receipt(
            {"readReceiptKey": {"id": "message-1", "fromMe": False}}
        )

    session.post.assert_not_called()


@pytest.mark.asyncio
async def test_whatsapp_detached_receipt_uses_fresh_lease_before_http():
    provider = _SequencedProvider(GateState.OPEN, GateState.RELOAD_ONLY)
    _configure(provider)
    adapter = _whatsapp_adapter()
    session = MagicMock()
    session.post = MagicMock()
    adapter._http_session = session
    payload = {"readReceiptKey": {"id": "message-1", "fromMe": False}}

    with acquire_outer_turn(_outer_request()):
        task = getattr(adapter, "_schedule_read_receipt")(payload)
        await task

    assert len(provider.requests) == 2
    assert provider.requests[1].entrypoint == "whatsapp-read-receipt"
    session.post.assert_not_called()
    assert len(provider.released) == 2
