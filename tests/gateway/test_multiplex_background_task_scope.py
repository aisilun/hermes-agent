"""Regression: background tasks respect profile secret scope when multiplexing.

Issue #60726: /background spawns _run_background_task as a fire-and-forget
asyncio task with no profile scope, so _resolve_session_agent_runtime()'s
credential reads raise UnscopedSecretError when multiplex_profiles is on.
The fix wraps the task body in _profile_runtime_scope, mirroring _run_agent.
"""
import asyncio
from contextlib import contextmanager
from pathlib import Path
from unittest import mock

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

    def test_builds_turn_gate_identity_inside_profile_scope(self):
        runner = _make_runner(multiplex=True)
        events = []

        async def _inner(*_args, **_kwargs):
            events.append("inner")

        runner._run_background_task_inner = mock.AsyncMock(side_effect=_inner)
        source = mock.MagicMock()
        source.profile = "test_profile"

        @contextmanager
        def _scope(_profile_home):
            events.append("scope-enter")
            try:
                yield
            finally:
                events.append("scope-exit")

        def _identity(**_kwargs):
            events.append("identity")
            return None

        @contextmanager
        def _lease(_request):
            events.append("lease")
            yield None

        with mock.patch.object(
            GatewayRunner,
            "_resolve_profile_home_for_source",
            return_value=Path("/fake/profile"),
        ), mock.patch(
            "gateway.run._profile_runtime_scope", new=_scope
        ), mock.patch(
            "agent.turn_gate.build_runtime_identity", side_effect=_identity
        ), mock.patch(
            "agent.turn_gate.acquire_outer_turn", new=_lease
        ):
            asyncio.run(
                runner._run_background_task(
                    prompt="test", source=source, task_id="bg_test"
                )
            )

        assert events == [
            "scope-enter",
            "identity",
            "lease",
            "inner",
            "scope-exit",
        ]


