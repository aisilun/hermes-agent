"""Tests for Feishu interactive card approval buttons."""

import asyncio
import importlib.util
import json
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Ensure the repo root is importable
# ---------------------------------------------------------------------------
_repo = str(Path(__file__).resolve().parents[2])
if _repo not in sys.path:
    sys.path.insert(0, _repo)


# ---------------------------------------------------------------------------
# Minimal Feishu mock so FeishuAdapter can be imported without lark-oapi
# ---------------------------------------------------------------------------
def _ensure_feishu_mocks():
    """Provide stubs for lark-oapi / aiohttp.web so the import succeeds."""
    if importlib.util.find_spec("lark_oapi") is None and "lark_oapi" not in sys.modules:
        mod = MagicMock()
        for name in (
            "lark_oapi", "lark_oapi.api.im.v1",
            "lark_oapi.event", "lark_oapi.event.callback_type",
        ):
            sys.modules.setdefault(name, mod)
    if importlib.util.find_spec("aiohttp") is None and "aiohttp" not in sys.modules:
        aio = MagicMock()
        sys.modules.setdefault("aiohttp", aio)
        sys.modules.setdefault("aiohttp.web", aio.web)


_ensure_feishu_mocks()

from gateway.config import PlatformConfig
import plugins.platforms.feishu.adapter as feishu_module
from plugins.platforms.feishu.adapter import FeishuAdapter


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_adapter() -> FeishuAdapter:
    """Create a FeishuAdapter with mocked internals."""
    config = PlatformConfig(enabled=True)
    adapter = FeishuAdapter(config)
    adapter._client = MagicMock()
    return adapter


def _make_card_action_data(
    action_value: dict,
    chat_id: str = "oc_12345",
    message_id: str = "",
    open_id: str = "ou_user1",
    token: str = "tok_abc",
) -> SimpleNamespace:
    """Create a mock Feishu card action callback data object."""
    return SimpleNamespace(
        event=SimpleNamespace(
            token=token,
            context=SimpleNamespace(
                open_chat_id=chat_id,
                open_message_id=message_id,
            ),
            operator=SimpleNamespace(open_id=open_id),
            action=SimpleNamespace(
                tag="button",
                value=action_value,
            ),
        ),
    )


def _close_submitted_coro(coro, _loop):
    """Close scheduled coroutines in sync-handler tests to avoid unawaited warnings."""
    coro.close()
    return SimpleNamespace(add_done_callback=lambda *_args, **_kwargs: None)


# ===========================================================================
# send_exec_approval — interactive card with buttons
# ===========================================================================

class TestFeishuExecApproval:
    """Test send_exec_approval sends an interactive card."""

    @pytest.mark.asyncio
    async def test_sends_interactive_card(self):
        adapter = _make_adapter()

        mock_response = SimpleNamespace(
            success=lambda: True,
            data=SimpleNamespace(message_id="msg_001"),
        )
        with patch.object(
            adapter, "_feishu_send_with_retry", new_callable=AsyncMock,
            return_value=mock_response,
        ) as mock_send:
            result = await adapter.send_exec_approval(
                chat_id="oc_12345",
                command="rm -rf /important",
                session_key="agent:main:feishu:group:oc_12345",
                description="dangerous deletion",
            )

        assert result.success is True
        assert result.message_id == "msg_001"

        mock_send.assert_called_once()
        kwargs = mock_send.call_args[1]
        assert kwargs["chat_id"] == "oc_12345"
        assert kwargs["msg_type"] == "interactive"

        # Verify card payload contains the command and buttons
        card = json.loads(kwargs["payload"])
        assert card["header"]["template"] == "orange"
        assert "rm -rf /important" in card["elements"][0]["content"]
        assert "删除操作" in card["elements"][0]["content"]

        # Check buttons
        actions = card["elements"][1]["actions"]
        assert len(actions) == 4
        action_names = [a["value"]["hermes_action"] for a in actions]
        assert action_names == [
            "approve_once", "approve_session", "approve_always", "deny"
        ]

    @pytest.mark.asyncio
    async def test_stores_approval_state(self):
        adapter = _make_adapter()

        mock_response = SimpleNamespace(
            success=lambda: True,
            data=SimpleNamespace(message_id="msg_002"),
        )
        with patch.object(
            adapter, "_feishu_send_with_retry", new_callable=AsyncMock,
            return_value=mock_response,
        ):
            await adapter.send_exec_approval(
                chat_id="oc_12345",
                command="echo test",
                session_key="my-session-key",
            )

        assert len(adapter._approval_state) == 1
        approval_id = list(adapter._approval_state.keys())[0]
        state = adapter._approval_state[approval_id]
        assert state["session_key"] == "my-session-key"
        assert state["message_id"] == "msg_002"
        assert state["chat_id"] == "oc_12345"


# ===========================================================================
# send_update_prompt — interactive card with buttons
# ===========================================================================

class TestFeishuUpdatePrompt:
    """Test send_update_prompt sends an interactive card."""

    @pytest.mark.asyncio
    async def test_sends_interactive_card(self):
        adapter = _make_adapter()

        mock_response = SimpleNamespace(
            success=lambda: True,
            data=SimpleNamespace(message_id="msg_up_001"),
        )
        with patch.object(
            adapter, "_feishu_send_with_retry", new_callable=AsyncMock,
            return_value=mock_response,
        ) as mock_send:
            result = await adapter.send_update_prompt(
                chat_id="oc_12345",
                prompt="Restore stashed changes after update?",
                default="y",
                session_key="agent:main:feishu:group:oc_12345",
                metadata={"thread_id": "th_1"},
            )

        assert result.success is True
        assert result.message_id == "msg_up_001"

        kwargs = mock_send.call_args[1]
        assert kwargs["chat_id"] == "oc_12345"
        assert kwargs["msg_type"] == "interactive"
        assert kwargs["metadata"] == {"thread_id": "th_1"}

        card = json.loads(kwargs["payload"])
        assert card["header"]["template"] == "orange"
        assert "Restore stashed changes after update?" in card["elements"][0]["content"]
        assert "Default: `y`" in card["elements"][0]["content"]
        actions = card["elements"][1]["actions"]
        assert [a["value"]["hermes_update_prompt_action"] for a in actions] == ["y", "n"]


# ===========================================================================
# _resolve_approval — approval state pop + gateway resolution
# ===========================================================================

class TestResolveApproval:
    """Test _resolve_approval pops state and calls resolve_gateway_approval."""

    @pytest.mark.asyncio
    async def test_resolves_once(self):
        adapter = _make_adapter()
        adapter._approval_state["nonce-1"] = {
            "session_key": "agent:main:feishu:group:oc_12345",
            "message_id": "msg_001",
            "chat_id": "oc_12345",
        }

        with patch("tools.approval.resolve_gateway_approval", return_value=1) as mock_resolve:
            await adapter._resolve_approval(
                "nonce-1",
                "once",
                "Norbert",
                open_id="ou_user1",
                chat_id="oc_12345",
                message_id="msg_001",
            )

        mock_resolve.assert_called_once_with("agent:main:feishu:group:oc_12345", "once")
        assert "nonce-1" not in adapter._approval_state


    @pytest.mark.asyncio
    async def test_unauthorized_click_does_not_resolve(self):
        adapter = _make_adapter()
        adapter._admins = {"ou_admin"}
        adapter._approval_state["nonce-5"] = {
            "session_key": "sess-5",
            "message_id": "msg_005",
            "chat_id": "oc_12345",
        }

        with patch("tools.approval.resolve_gateway_approval") as mock_resolve:
            await adapter._resolve_approval(
                "nonce-5",
                "once",
                "Mallory",
                open_id="ou_intruder",
                chat_id="oc_12345",
                message_id="msg_005",
            )

        mock_resolve.assert_not_called()
        assert "nonce-5" in adapter._approval_state


# ===========================================================================
# _handle_card_action_event — non-approval card actions
# ===========================================================================

class TestNonApprovalCardAction:
    """Non-approval card actions should still route as synthetic commands."""

    @pytest.mark.asyncio
    async def test_routes_as_synthetic_command(self):
        adapter = _make_adapter()

        data = _make_card_action_data(
            action_value={"custom_action": "something_else"},
            token="tok_normal",
        )

        with (
            patch.object(
                adapter, "_resolve_sender_profile", new_callable=AsyncMock,
                return_value={"user_id": "ou_u", "user_name": "Dave", "user_id_alt": None},
            ),
            patch.object(adapter, "get_chat_info", new_callable=AsyncMock, return_value={"name": "Test Chat"}),
            patch.object(adapter, "_handle_message_with_guards", new_callable=AsyncMock) as mock_handle,
        ):
            await adapter._handle_card_action_event(data)

        mock_handle.assert_called_once()
        event = mock_handle.call_args[0][0]
        assert "/card button" in event.text


# ===========================================================================
# _on_card_action_trigger — inline card response for approval actions
# ===========================================================================

class _FakeCallBackCard:
    def __init__(self):
        self.type = None
        self.data = None


class _FakeP2Response:
    def __init__(self):
        self.card = None


@pytest.fixture(autouse=False)
def _patch_callback_card_types(monkeypatch):
    """Provide real-ish P2CardActionTriggerResponse / CallBackCard for tests."""
    monkeypatch.setattr(feishu_module, "P2CardActionTriggerResponse", _FakeP2Response)
    monkeypatch.setattr(feishu_module, "CallBackCard", _FakeCallBackCard)


class TestCardActionCallbackResponse:
    """Test that _on_card_action_trigger returns updated card inline."""

    def test_drops_action_when_loop_not_ready(self, _patch_callback_card_types):
        adapter = _make_adapter()
        adapter._loop = None
        data = _make_card_action_data({"hermes_action": "approve_once", "approval_id": 1})

        with patch("asyncio.run_coroutine_threadsafe") as mock_submit:
            response = adapter._on_card_action_trigger(data)

        assert response is not None
        assert response.card is None
        mock_submit.assert_not_called()

    def test_returns_card_for_approve_action(self, _patch_callback_card_types):
        adapter = _make_adapter()
        adapter._loop = MagicMock()
        adapter._loop.is_closed = MagicMock(return_value=False)
        adapter._allowed_group_users = {"ou_bob"}
        adapter._approval_state["nonce-1"] = {
            "session_key": "sess-1",
            "message_id": "msg-1",
            "chat_id": "oc_12345",
        }
        data = _make_card_action_data(
            {"hermes_action": "approve_once", "approval_id": "nonce-1"},
            message_id="msg-1",
            open_id="ou_bob",
        )
        adapter._sender_name_cache["ou_bob"] = ("Bob", 9999999999)

        with patch("asyncio.run_coroutine_threadsafe", side_effect=_close_submitted_coro):
            response = adapter._on_card_action_trigger(data)

        assert response is not None
        assert response.card is not None
        assert response.card.type == "raw"
        card = response.card.data
        assert card["header"]["template"] == "green"
        assert "已批准（仅本次）" in card["header"]["title"]["content"]
        assert "Bob" in card["elements"][0]["content"]


    def test_ignores_expired_cached_name(self, _patch_callback_card_types):
        adapter = _make_adapter()
        adapter._loop = MagicMock()
        adapter._loop.is_closed = MagicMock(return_value=False)
        adapter._allowed_group_users = {"ou_expired"}
        adapter._approval_state["nonce-4"] = {
            "session_key": "sess-4",
            "message_id": "msg-4",
            "chat_id": "oc_12345",
        }
        data = _make_card_action_data(
            {"hermes_action": "approve_once", "approval_id": "nonce-4"},
            message_id="msg-4",
            open_id="ou_expired",
        )
        adapter._sender_name_cache["ou_expired"] = ("Old Name", 1)

        with patch("asyncio.run_coroutine_threadsafe", side_effect=_close_submitted_coro):
            response = adapter._on_card_action_trigger(data)

        card = response.card.data
        assert "Old Name" not in card["elements"][0]["content"]
        assert "ou_expired" in card["elements"][0]["content"]

    def test_rejects_approval_click_from_unauthorized_user(
        self, _patch_callback_card_types
    ):
        adapter = _make_adapter()
        adapter._loop = MagicMock()
        adapter._loop.is_closed = MagicMock(return_value=False)
        adapter._allowed_group_users = {"ou_allowed"}
        adapter._approval_state["nonce-5"] = {
            "session_key": "sess-5",
            "message_id": "msg-5",
            "chat_id": "oc_12345",
        }
        data = _make_card_action_data(
            {"hermes_action": "approve_once", "approval_id": "nonce-5"},
            message_id="msg-5",
            open_id="ou_attacker",
        )

        with patch("asyncio.run_coroutine_threadsafe") as mock_submit:
            response = adapter._on_card_action_trigger(data)

        assert response is not None
        assert response.card is None
        assert "nonce-5" in adapter._approval_state
        mock_submit.assert_not_called()


    def test_update_prompt_unauthorized_operator_returns_no_card(
        self, _patch_callback_card_types
    ):
        adapter = _make_adapter()
        adapter._loop = MagicMock()
        adapter._loop.is_closed = MagicMock(return_value=False)
        adapter._update_prompt_state["prompt-1"] = {
            "session_key": "sess-up-1",
            "message_id": "msg_up_006",
            "chat_id": "oc_12345",
        }
        adapter._allowed_group_users = {"ou_allowed"}
        data = _make_card_action_data(
            {"hermes_update_prompt_action": "y", "update_prompt_id": "prompt-1"},
            message_id="msg_up_006",
            open_id="ou_intruder",
        )

        with patch("asyncio.run_coroutine_threadsafe") as mock_submit:
            response = adapter._on_card_action_trigger(data)

        assert response is not None
        assert response.card is None
        assert "prompt-1" in adapter._update_prompt_state
        mock_submit.assert_not_called()


    def test_update_prompt_chat_mismatch_returns_no_card(self, _patch_callback_card_types):
        adapter = _make_adapter()
        adapter._loop = MagicMock()
        adapter._loop.is_closed = MagicMock(return_value=False)
        adapter._allowed_group_users = {"ou_bob"}
        adapter._update_prompt_state["prompt-8"] = {
            "session_key": "sess-up-8",
            "message_id": "msg_up_008",
            "chat_id": "oc_expected",
        }
        data = _make_card_action_data(
            {"hermes_update_prompt_action": "y", "update_prompt_id": "prompt-8"},
            chat_id="oc_mismatch",
            message_id="msg_up_008",
            open_id="ou_bob",
        )

        with patch("asyncio.run_coroutine_threadsafe") as mock_submit:
            response = adapter._on_card_action_trigger(data)

        assert response is not None
        assert response.card.data["header"]["template"] == "red"
        assert "prompt-8" in adapter._update_prompt_state
        mock_submit.assert_not_called()


class TestResolveUpdatePrompt:
    """Test update prompt resolution persists the response file."""

    @pytest.mark.asyncio
    async def test_writes_response_file(self, tmp_path, monkeypatch):
        adapter = _make_adapter()
        monkeypatch.setenv("HERMES_HOME", str(tmp_path / ".hermes"))
        (tmp_path / ".hermes").mkdir()
        adapter._update_prompt_state["prompt-1"] = {
            "session_key": "sess-up-1",
            "message_id": "msg_up_003",
            "chat_id": "oc_12345",
        }

        await adapter._resolve_update_prompt(
            "prompt-1",
            "y",
            "Alice",
            chat_id="oc_12345",
            message_id="msg_up_003",
        )

        assert (tmp_path / ".hermes" / ".update_response").read_text() == "y"
        assert "prompt-1" not in adapter._update_prompt_state


@pytest.mark.asyncio
async def test_uses_chinese_labels_and_destructive_risk_summary():
    adapter = _make_adapter()
    mock_response = SimpleNamespace(
        success=lambda: True,
        data=SimpleNamespace(message_id="msg_zh_001"),
    )
    with patch.object(
        adapter, "_feishu_send_with_retry", new_callable=AsyncMock,
        return_value=mock_response,
    ) as mock_send:
        await adapter.send_exec_approval(
            chat_id="oc_12345",
            command="rm -rf /important",
            session_key="s",
            description="script execution via -e/-c flag",
        )

    card = json.loads(mock_send.call_args[1]["payload"])
    assert card["header"]["title"]["content"] == "⚠️ 危险命令审批"
    content = card["elements"][0]["content"]
    assert "风险等级：高风险" in content
    assert "影响范围：可能删除或覆盖文件、目录或数据" in content
    assert "不可逆性：可能不可逆" in content
    assert "建议动作：仅本次批准" in content
    assert "脚本参数（-e/-c）" in content
    assert "script execution via -e/-c flag" not in content
    assert r"\n" not in content

    actions = card["elements"][1]["actions"]
    assert [a["text"]["content"] for a in actions] == [
        "✅ 仅本次批准", "✅ 本会话批准", "⚠️ 永久允许", "❌ 拒绝"
    ]


@pytest.mark.asyncio
async def test_uses_conservative_low_and_unknown_risk_labels():
    adapter = _make_adapter()
    mock_response = SimpleNamespace(
        success=lambda: True,
        data=SimpleNamespace(message_id="msg_zh_002"),
    )
    with patch.object(
        adapter, "_feishu_send_with_retry", new_callable=AsyncMock,
        return_value=mock_response,
    ) as mock_send:
        await adapter.send_exec_approval(
            chat_id="oc_12345", command="git status", session_key="s"
        )
        low_card = json.loads(mock_send.call_args[1]["payload"])
        await adapter.send_exec_approval(
            chat_id="oc_12345", command="custom_internal_operation --run", session_key="s"
        )
        unknown_card = json.loads(mock_send.call_args[1]["payload"])

    low_content = low_card["elements"][0]["content"]
    assert "风险等级：低风险候选（规则识别为只读）" in low_content
    assert "预计不修改文件或配置" in low_content
    assert "建议动作：仅本次批准" in low_content

    unknown_content = unknown_card["elements"][0]["content"]
    assert "风险等级：待人工确认" in unknown_content
    assert "不能根据当前规则确认安全" in unknown_content
    assert "建议动作：仅本次批准" in unknown_content


@pytest.mark.asyncio
async def test_smart_denied_keeps_only_one_shot_override():
    adapter = _make_adapter()
    mock_response = SimpleNamespace(
        success=lambda: True,
        data=SimpleNamespace(message_id="msg_zh_003"),
    )
    with patch.object(
        adapter, "_feishu_send_with_retry", new_callable=AsyncMock,
        return_value=mock_response,
    ) as mock_send:
        await adapter.send_exec_approval(
            chat_id="oc_12345",
            command="rm -rf /important",
            session_key="s",
            smart_denied=True,
        )

    card = json.loads(mock_send.call_args[1]["payload"])
    actions = card["elements"][1]["actions"]
    assert [a["value"]["hermes_action"] for a in actions] == [
        "approve_once", "deny"
    ]
    assert "智能审批建议拒绝" in card["elements"][0]["content"]


class TestApprovalNonceAndMessageBinding:
    @pytest.mark.asyncio
    async def test_generated_approval_and_prompt_ids_are_opaque_strings(self):
        adapter = _make_adapter()
        responses = [
            SimpleNamespace(success=lambda: True, data=SimpleNamespace(message_id="msg-a")),
            SimpleNamespace(success=lambda: True, data=SimpleNamespace(message_id="msg-b")),
        ]
        with patch.object(
            adapter,
            "_feishu_send_with_retry",
            new_callable=AsyncMock,
            side_effect=responses,
        ) as send:
            await adapter.send_exec_approval("oc_12345", "date", "sess-a")
            approval_card = json.loads(send.call_args_list[0].kwargs["payload"])
            await adapter.send_update_prompt("oc_12345", "continue?", session_key="sess-b")
            prompt_card = json.loads(send.call_args_list[1].kwargs["payload"])

        approval_id = approval_card["elements"][1]["actions"][0]["value"]["approval_id"]
        prompt_id = prompt_card["elements"][1]["actions"][0]["value"]["update_prompt_id"]
        assert isinstance(approval_id, str) and len(approval_id) >= 20
        assert isinstance(prompt_id, str) and len(prompt_id) >= 20
        assert approval_id in adapter._approval_state
        assert prompt_id in adapter._update_prompt_state

    @pytest.mark.parametrize("callback_message_id", ["", "msg-wrong"])
    def test_approval_rejects_missing_or_mismatched_message_id(
        self,
        _patch_callback_card_types,
        callback_message_id,
    ):
        adapter = _make_adapter()
        adapter._allowed_group_users = {"ou_user1"}
        adapter._loop = MagicMock()
        adapter._loop.is_closed.return_value = False
        nonce = "opaque-approval-nonce"
        adapter._approval_state[nonce] = {
            "session_key": "sess",
            "message_id": "msg-exact",
            "chat_id": "oc_12345",
        }
        data = _make_card_action_data(
            {"hermes_action": "approve_once", "approval_id": nonce},
            message_id=callback_message_id,
        )

        with patch.object(adapter, "_submit_on_loop") as submit:
            response = adapter._on_card_action_trigger(data)

        submit.assert_not_called()
        assert nonce in adapter._approval_state
        assert response.card.type == "raw"
        assert response.card.data["header"]["template"] == "red"
        assert "拒绝" in response.card.data["header"]["title"]["content"]

    def test_exact_approval_triple_schedules_once_and_second_click_rejects(
        self,
        _patch_callback_card_types,
    ):
        adapter = _make_adapter()
        adapter._allowed_group_users = {"ou_user1"}
        adapter._loop = MagicMock()
        adapter._loop.is_closed.return_value = False
        nonce = "opaque-approval-nonce"
        adapter._approval_state[nonce] = {
            "session_key": "sess",
            "message_id": "msg-exact",
            "chat_id": "oc_12345",
        }
        data = _make_card_action_data(
            {"hermes_action": "approve_once", "approval_id": nonce},
            message_id="msg-exact",
        )

        with patch.object(adapter, "_submit_on_loop", side_effect=_close_submitted_coro) as submit:
            first = adapter._on_card_action_trigger(data)
            second = adapter._on_card_action_trigger(data)

        assert submit.call_count == 1
        assert first.card.data["header"]["template"] == "green"
        assert second.card.data["header"]["template"] == "red"

    def test_adapter_rebuild_rejects_old_card(self, _patch_callback_card_types):
        adapter = _make_adapter()
        adapter._loop = MagicMock()
        adapter._loop.is_closed.return_value = False
        data = _make_card_action_data(
            {"hermes_action": "approve_once", "approval_id": "old-process-nonce"},
            message_id="old-message",
        )
        with patch.object(adapter, "_submit_on_loop") as submit:
            response = adapter._on_card_action_trigger(data)
        submit.assert_not_called()
        assert response.card.data["header"]["template"] == "red"

    def test_update_prompt_requires_exact_message_and_rejects_second_click(
        self,
        _patch_callback_card_types,
    ):
        adapter = _make_adapter()
        adapter._allowed_group_users = {"ou_user1"}
        adapter._loop = MagicMock()
        adapter._loop.is_closed.return_value = False
        nonce = "opaque-update-nonce"
        adapter._update_prompt_state[nonce] = {
            "session_key": "sess",
            "message_id": "msg-update",
            "chat_id": "oc_12345",
        }
        wrong = _make_card_action_data(
            {"hermes_update_prompt_action": "y", "update_prompt_id": nonce},
            message_id="msg-old-card",
        )
        exact = _make_card_action_data(
            {"hermes_update_prompt_action": "y", "update_prompt_id": nonce},
            message_id="msg-update",
        )

        with patch.object(adapter, "_submit_on_loop", side_effect=_close_submitted_coro) as submit:
            rejected = adapter._on_card_action_trigger(wrong)
            accepted = adapter._on_card_action_trigger(exact)
            duplicate = adapter._on_card_action_trigger(exact)

        assert submit.call_count == 1
        assert rejected.card.data["header"]["template"] == "red"
        assert accepted.card.data["header"]["template"] == "green"
        assert duplicate.card.data["header"]["template"] == "red"

    @pytest.mark.asyncio
    async def test_async_resolver_rechecks_message_and_consumes_at_most_once(self):
        adapter = _make_adapter()
        nonce = "opaque-approval-nonce"
        adapter._approval_state[nonce] = {
            "session_key": "sess",
            "message_id": "msg-exact",
            "chat_id": "oc_12345",
        }

        with patch("tools.approval.resolve_gateway_approval", return_value=1) as resolve:
            await adapter._resolve_approval(
                nonce,
                "once",
                "Alice",
                open_id="ou_user1",
                chat_id="oc_12345",
                message_id="msg-wrong",
            )
            assert nonce in adapter._approval_state
            await asyncio.gather(
                adapter._resolve_approval(
                    nonce,
                    "once",
                    "Alice",
                    open_id="ou_user1",
                    chat_id="oc_12345",
                    message_id="msg-exact",
                ),
                adapter._resolve_approval(
                    nonce,
                    "once",
                    "Alice",
                    open_id="ou_user1",
                    chat_id="oc_12345",
                    message_id="msg-exact",
                ),
            )

        resolve.assert_called_once_with("sess", "once")
        assert nonce not in adapter._approval_state


