"""Regression: background tasks respect profile secret scope when multiplexing.

Issue #60726: /background spawns _run_background_task as a fire-and-forget
asyncio task with no profile scope, so _resolve_session_agent_runtime()'s
credential reads raise UnscopedSecretError when multiplex_profiles is on.
The fix wraps the task body in _profile_runtime_scope, mirroring _run_agent.
"""
import asyncio
from pathlib import Path
from unittest import mock

from agent.turn_gate import (
    GateDecision,
    GateState,
    clear_turn_gate_registry_for_testing,
    configure_turn_gate_from_config,
    register_turn_gate_provider,
)
from gateway.config import GatewayConfig
from gateway.run import GatewayRunner


def _make_runner(multiplex: bool) -> GatewayRunner:
    runner = GatewayRunner.__new__(GatewayRunner)
    runner.config = GatewayConfig(multiplex_profiles=multiplex)
    return runner


class TestBackgroundTaskProfileScope:
    """_run_background_task installs _profile_runtime_scope when multiplexing is active."""

    def test_wraps_in_profile_scope_when_multiplex_active(self):
        runner = _make_runner(multiplex=True)
        inner = mock.AsyncMock(return_value=None)
        runner._run_background_task_inner = inner

        source = mock.MagicMock()
        source.profile = "test_profile"

        with mock.patch.object(
            GatewayRunner,
            "_resolve_profile_home_for_source",
            return_value=Path("/fake/profile"),
        ), mock.patch("gateway.run._profile_runtime_scope") as scope:
            scope.return_value.__enter__ = mock.MagicMock()
            scope.return_value.__exit__ = mock.MagicMock(return_value=False)
            asyncio.run(
                runner._run_background_task(
                    prompt="test", source=source, task_id="bg_test"
                )
            )

        scope.assert_called_once_with(Path("/fake/profile"))
        inner.assert_awaited_once()

    def test_gate_identity_is_built_inside_target_profile_scope(self):
        runner = _make_runner(multiplex=True)
        body_profiles: list[str] = []

        class Provider:
            def __init__(self):
                self.profiles: list[str] = []

            def acquire(self, request):
                self.profiles.append(request.identity.profile)
                return GateDecision(
                    provider_id="test-gate",
                    state=GateState.OPEN,
                    lease_id="lease-background-profile",
                    generation=1,
                )

            def validate(self, decision, checkpoint):
                return decision

            def release(self, decision):
                return None

        async def inner(*_args, **_kwargs):
            from hermes_cli.profiles import get_active_profile_name

            body_profiles.append(get_active_profile_name())

        provider = Provider()
        source = mock.MagicMock()
        source.profile = "probe_profile"
        profile_root = Path.home() / ".hermes-test-profiles"
        profile_home = profile_root / "probe-profile"
        runner._run_background_task_inner = mock.AsyncMock(side_effect=inner)

        clear_turn_gate_registry_for_testing()
        try:
            register_turn_gate_provider(
                "test-gate",
                provider,
                owner_id="test-gate",
            )
            configure_turn_gate_from_config(
                {
                    "agent": {
                        "turn_gate": {
                            "required_provider": "test-gate",
                            "runtime_identity": {"machine_id": "test-machine"},
                        }
                    }
                }
            )
            with mock.patch.object(
                GatewayRunner,
                "_resolve_profile_home_for_source",
                return_value=profile_home,
            ), mock.patch(
                "hermes_cli.profiles._get_profiles_root",
                return_value=profile_root,
            ):
                asyncio.run(
                    runner._run_background_task(
                        prompt="test",
                        source=source,
                        task_id="bg-profile-gate",
                    )
                )
        finally:
            clear_turn_gate_registry_for_testing()

        assert provider.profiles == ["probe-profile"]
        assert body_profiles == ["probe-profile"]


