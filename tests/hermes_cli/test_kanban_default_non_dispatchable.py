"""Gate-3 contract: shared Kanban never auto-dispatches ``default``."""

from __future__ import annotations

from pathlib import Path
import sqlite3
from types import SimpleNamespace

import pytest

from hermes_cli import kanban_db as kb

POLICY = "privileged_profile_auto_dispatch_disabled"


@pytest.fixture
def shared_kanban(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    home = tmp_path / ".hermes"
    home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    kb._INITIALIZED_PATHS.clear()
    kb.init_db()
    return home


def _task(conn, task_id: str) -> kb.Task:
    task = kb.get_task(conn, task_id)
    assert task is not None
    return task


def _insert_legacy_default(conn, *, status: str) -> str:
    task_id = kb.create_task(
        conn,
        title=f"legacy {status}",
        assignee="worker-a",
        created_by="worker-a",
    )
    conn.execute(
        "UPDATE tasks SET assignee = 'default', created_by = 'default', "
        "status = ?, claim_lock = NULL WHERE id = ?",
        (status, task_id),
    )
    conn.commit()
    return task_id


def _assert_capability_block(conn, task_id: str) -> None:
    task = _task(conn, task_id)
    assert task.status == "blocked"
    assert task.block_kind == "capability"
    events = kb.list_events(conn, task_id)
    assert any(
        event.payload and event.payload.get("policy") == POLICY for event in events
    )


@pytest.mark.parametrize("created_by", ["default", "worker-a", None])
def test_created_by_is_audit_only_and_cannot_authorize_executable_default(
    shared_kanban: Path,
    created_by: str | None,
) -> None:
    with kb.connect_closing() as conn:
        with pytest.raises(kb.PrivilegedDelegationError, match=POLICY):
            kb.create_task(
                conn,
                title="must not execute",
                assignee="default",
                created_by=created_by,
            )


def test_blocked_default_control_card_persists_but_cannot_unblock_or_promote(
    shared_kanban: Path,
) -> None:
    with kb.connect_closing() as conn:
        task_id = kb.create_task(
            conn,
            title="authorization control card",
            assignee="default",
            created_by="spoofable-audit-label",
            initial_status="blocked",
        )
        assert _task(conn, task_id).created_by == "spoofable-audit-label"
        assert kb.unblock_task(conn, task_id) is False
        promoted, reason = kb.promote_task(conn, task_id, actor="operator", force=True)
        assert promoted is False
        assert reason and POLICY in reason
        assert kb.recompute_ready(conn) == 0
        assert kb.claim_task(conn, task_id) is None
        _assert_capability_block(conn, task_id)


@pytest.mark.parametrize(
    ("status", "claim"),
    [("ready", kb.claim_task), ("review", kb.claim_review_task)],
)
def test_legacy_default_rows_are_blocked_at_claim_even_when_created_by_default(
    shared_kanban: Path,
    status: str,
    claim,
) -> None:
    with kb.connect_closing() as conn:
        task_id = _insert_legacy_default(conn, status=status)
        assert claim(conn, task_id) is None
        _assert_capability_block(conn, task_id)


def test_legacy_blocked_default_without_sticky_event_never_auto_promotes(
    shared_kanban: Path,
) -> None:
    with kb.connect_closing() as conn:
        task_id = _insert_legacy_default(conn, status="blocked")
        assert kb.recompute_ready(conn) == 0
        assert _task(conn, task_id).status == "blocked"


def test_assign_cannot_route_any_task_into_default(shared_kanban: Path) -> None:
    with kb.connect_closing() as conn:
        task_id = kb.create_task(
            conn,
            title="ordinary work",
            assignee="worker-a",
            created_by="default",
        )
        with pytest.raises(kb.PrivilegedDelegationError, match=POLICY):
            kb.assign_task(conn, task_id, "default")
        assert _task(conn, task_id).assignee == "worker-a"


def test_dispatch_and_default_spawn_are_final_capability_blocks(
    shared_kanban: Path,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from hermes_cli import profiles

    monkeypatch.setattr(profiles, "profile_exists", lambda _name: True)
    spawned: list[str] = []
    with kb.connect_closing() as conn:
        task_id = _insert_legacy_default(conn, status="ready")
        task = _task(conn, task_id)
        result = kb.dispatch_once(
            conn,
            spawn_fn=lambda t, _workspace, board=None: spawned.append(t.id) or 42,
        )
        assert spawned == []
        assert task_id in result.auto_blocked
        _assert_capability_block(conn, task_id)

    with pytest.raises(kb.PrivilegedDelegationError, match=POLICY):
        kb._default_spawn(task, str(tmp_path))


def test_default_assignee_default_blocks_unassigned_task(shared_kanban: Path) -> None:
    with kb.connect_closing() as conn:
        task_id = kb.create_task(conn, title="unassigned", created_by="default")
        result = kb.dispatch_once(
            conn,
            spawn_fn=lambda *_args, **_kwargs: 42,
            default_assignee="default",
        )
        assert result.spawned == []
        assert task_id in result.auto_blocked
        _assert_capability_block(conn, task_id)


def test_decompose_fallback_never_selects_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from hermes_cli import kanban_decompose as decompose

    profiles = [
        SimpleNamespace(name="default", description="privileged"),
        SimpleNamespace(name="worker-a", description="worker"),
    ]
    monkeypatch.setattr(decompose.profiles_mod, "list_profiles", lambda: profiles)
    monkeypatch.setattr(decompose.profiles_mod, "profile_exists", lambda _name: True)
    monkeypatch.setattr(
        decompose.profiles_mod, "get_active_profile_name", lambda: "default"
    )

    assert (
        decompose._resolve_default_assignee({"kanban": {"default_assignee": "default"}})
        == "worker-a"
    )
    assert (
        decompose._normalize_assignee_choice(
            None,
            default_assignee="worker-a",
            valid_names={"default", "worker-a"},
        )
        == "worker-a"
    )

    monkeypatch.setattr(
        decompose.profiles_mod,
        "list_profiles",
        lambda: [SimpleNamespace(name="default", description="operator")],
    )
    assert (
        decompose._resolve_default_assignee({"kanban": {"default_assignee": "default"}})
        == ""
    )


def test_nonprivileged_profile_flow_is_unchanged(shared_kanban: Path) -> None:
    with kb.connect_closing() as conn:
        task_id = kb.create_task(
            conn,
            title="ordinary",
            assignee="worker-a",
            created_by="default",
        )
        claimed = kb.claim_task(conn, task_id)
        assert claimed is not None
        assert claimed.assignee == "worker-a"


def test_genuinely_isolated_in_memory_db_keeps_legacy_local_flow() -> None:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(kb.SCHEMA_SQL)
    try:
        task_id = kb.create_task(
            conn,
            title="isolated local workflow",
            assignee="default",
            created_by="default",
        )
        claimed = kb.claim_task(conn, task_id)
        assert claimed is not None
        assert claimed.assignee == "default"
    finally:
        conn.close()
