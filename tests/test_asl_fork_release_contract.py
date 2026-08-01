"""Machine gates for the ASL-maintained Hermes fork candidate."""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
import tomllib
from pathlib import Path
from types import SimpleNamespace


ROOT = Path(__file__).resolve().parents[1]
CONTRACT_PATH = ROOT / "governance" / "asl-fork-release.json"
EXPECTED_VERSION = "0.19.1+asl.1"
EXPECTED_TAG = "v0.19.1-asl.1"
CURRENT_OFFICIAL_VERSION = "0.19.0+asl.3"
EXPECTED_REQUIRED_TESTS = {
    "tests/agent/test_turn_gate.py",
    "tests/agent/test_conversation_reload_gate.py",
    "tests/agent/test_tool_executor_reload_gate.py",
    "tests/agent/test_host_tool_env_bridge.py",
    "tests/agent/test_tool_observation_dispatch.py",
    "tests/agent/test_model_metadata.py",
    "tests/agent/test_conversation_compression_backup.py",
    "tests/gateway/test_reload_turn_gate.py",
    "tests/gateway/test_multiplex_background_task_scope.py",
    "tests/gateway/test_feishu_approval_buttons.py",
    "tests/gateway/test_session_info.py",
    "tests/hermes_cli/test_turn_gate_plugin.py",
    "tests/hermes_cli/test_gateway_service.py",
    "tests/hermes_cli/test_kanban_blocked_sticky.py",
    "tests/hermes_cli/test_kanban_db.py",
    "tests/hermes_cli/test_kanban_default_assignee.py",
    "tests/hermes_cli/test_kanban_privileged_delegation.py",
    "tests/test_asl_fork_release_contract.py",
    "tests/hermes_cli/test_banner_git_state.py",
    "tests/hermes_cli/test_cmd_update.py",
    "tests/hermes_cli/test_update_zip_symlink_reject.py",
    "tests/test_install_autostash_conflict_recovery.py",
}
EXPECTED_PATCH_QUEUE = [
    {
        "id": "host-turn-gate",
        "source_commits": ["0e1031a9ff05d0c0d2f44f2148b80a33ca9d3561"],
        "upstream_coverage": "missing",
        "decision": "retain",
    },
    {
        "id": "fork-source-update-channel",
        "source_commits": [
            "4f6b6fad6797d0c0f996234ce9555ae9f0a29b31",
            "0c5b6716659963f1fd4d992dfa6d10cf1e3a57e4",
        ],
        "upstream_coverage": "not-applicable",
        "decision": "retain",
    },
    {
        "id": "runtime-authorization-safeguards",
        "source_commits": ["7ad87b9c0cf1dbd2801b4eb7f03860bec3b795d0"],
        "upstream_coverage": "partial",
        "decision": "semantic-replay",
    },
    {
        "id": "asl-production-ci",
        "source_commits": ["16f97e2d3aca5d48a863b901e0a27c85d0132b83"],
        "upstream_coverage": "not-applicable",
        "decision": "retain",
    },
    {
        "id": "macos-no-start-install",
        "source_commits": ["9e152bcddc92d7130f07b413673c9c9380deb2b2"],
        "upstream_coverage": "partial",
        "decision": "retain-no-start-and-runtime-marker-ignore",
    },
    {
        "id": "kanban-privileged-delegation",
        "source_commits": ["d83815b361dd88e5e126fcece06ab6fe15290027"],
        "upstream_coverage": "missing",
        "decision": "retain",
    },
]


def _load_contract() -> dict:
    return json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))


def test_asl_fork_contract_is_closed_and_version_locked():
    contract = _load_contract()

    assert set(contract) == {
        "schema_version",
        "candidate",
        "release_state",
        "source",
        "maintenance",
        "patch_queue",
        "distribution",
        "verification",
        "review_gate",
        "authorization",
    }
    assert contract["schema_version"] == 2
    assert contract["candidate"] == {
        "repository": "aisilun/hermes-agent",
        "package_version": EXPECTED_VERSION,
        "planned_tag": EXPECTED_TAG,
        "status": "candidate",
        "prepared_at": "2026-08-01",
    }

    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    assert project["version"] == EXPECTED_VERSION

    init_text = (ROOT / "hermes_cli" / "__init__.py").read_text(encoding="utf-8")
    assert f'__version__ = "{EXPECTED_VERSION}"' in init_text
    assert '__release_date__ = "2026.8.1"' in init_text
    assert '__update_branch__ = "asl/production"' in init_text

    lock_text = (ROOT / "uv.lock").read_text(encoding="utf-8")
    assert f'name = "hermes-agent"\nversion = "{EXPECTED_VERSION}"' in lock_text


def test_asl_fork_contract_binds_exact_sources():
    source = _load_contract()["source"]
    assert set(source) == {
        "upstream_repository",
        "official_release_tag",
        "official_release_commit",
        "upstream_base_commit",
        "turn_gate_upstream_base_commit",
        "previous_asl_official_commit",
        "turn_gate_source_commit",
        "privileged_delegation_source_commit",
        "upstream_pull_request",
        "latest_upstream_main_observed",
    }
    assert source["upstream_repository"] == "NousResearch/hermes-agent"
    assert source["official_release_tag"] == "v2026.7.30"
    assert source["official_release_commit"] == "cc4cab2f592e60a197e796506de9168f74baf3ea"
    assert source["upstream_base_commit"] == source["official_release_commit"]
    assert source["turn_gate_upstream_base_commit"] == "0bd82a8a84595720ea1f14b103aeb81ca3cc50ef"
    assert source["previous_asl_official_commit"] == "d01f138cf889ed95e7b7ff3785b89db55c52e828"
    assert source["turn_gate_source_commit"] == "0e1031a9ff05d0c0d2f44f2148b80a33ca9d3561"
    assert source["privileged_delegation_source_commit"] == "d83815b361dd88e5e126fcece06ab6fe15290027"
    assert source["upstream_pull_request"] == "https://github.com/NousResearch/hermes-agent/pull/74529"
    assert source["latest_upstream_main_observed"] == {
        "commit": "e444d165807f489b5c1ab8e4a612c8d09c2e67a2",
        "observed_at": "2026-08-01",
        "included": False,
    }
    for key in (
        "official_release_commit",
        "upstream_base_commit",
        "turn_gate_upstream_base_commit",
        "previous_asl_official_commit",
        "turn_gate_source_commit",
        "privileged_delegation_source_commit",
    ):
        assert re.fullmatch(r"[0-9a-f]{40}", source[key])


def test_asl_fork_contract_defines_minimal_patch_queue():
    contract = _load_contract()
    assert contract["patch_queue"] == EXPECTED_PATCH_QUEUE
    assert len({entry["id"] for entry in contract["patch_queue"]}) == len(EXPECTED_PATCH_QUEUE)
    for entry in contract["patch_queue"]:
        assert entry["source_commits"]
        assert all(re.fullmatch(r"[0-9a-f]{40}", sha) for sha in entry["source_commits"])


def test_distribution_uses_the_fork_source_installer_not_python_artifacts():
    contract = _load_contract()
    distribution = contract["distribution"]

    assert distribution == {
        "method": "shell-installer-source-checkout",
        "installer_path": "scripts/install.sh",
        "default_https_repository": "https://github.com/aisilun/hermes-agent.git",
        "default_ssh_repository": "git@github.com:aisilun/hermes-agent.git",
        "default_branch": "asl/production",
        "planned_ref": EXPECTED_TAG,
        "unsupported_artifacts": ["wheel", "sdist", "pypi"],
    }

    installer = (ROOT / distribution["installer_path"]).read_text(encoding="utf-8")
    assert 'REPO_URL_HTTPS="https://github.com/aisilun/hermes-agent.git"' in installer
    assert 'REPO_URL_SSH="git@github.com:aisilun/hermes-agent.git"' in installer
    assert 'BRANCH="asl/production"' in installer
    assert "--branch NAME  Git branch to install (default: asl/production)" in installer
    assert 'git -c http.version=HTTP/1.1 clone --depth 1 --branch "$BRANCH"' in installer

    from hermes_cli import __update_branch__
    from hermes_cli.banner import (
        _RELEASE_URL_BASE,
        _UPDATE_BRANCH,
        _UPSTREAM_REPO_URL,
    )
    from hermes_cli.main import _resolve_update_branch

    assert __update_branch__ == distribution["default_branch"]
    assert _UPDATE_BRANCH == distribution["default_branch"]
    assert _UPSTREAM_REPO_URL == distribution["default_https_repository"]
    assert _RELEASE_URL_BASE == "https://github.com/aisilun/hermes-agent/releases/tag"
    assert _resolve_update_branch(SimpleNamespace(branch=None)) == "asl/production"
    assert _resolve_update_branch(SimpleNamespace(branch="feature/test")) == "feature/test"

    update_parser = (ROOT / "hermes_cli/subcommands/update.py").read_text(encoding="utf-8")
    assert "default (asl/production)" in update_parser

    setup_guard = (ROOT / "setup.py").read_text(encoding="utf-8")
    assert "Building wheels or sdists for hermes-agent is not supported" in setup_guard


def test_candidate_keeps_review_release_and_live_activation_closed():
    contract = _load_contract()
    assert contract["maintenance"] == {
        "owner": "aisilun",
        "upstream_tracking": "NousResearch/hermes-agent#74529",
        "reconciliation_policy": "official-tag-minimal-overlay",
    }
    assert contract["candidate"]["status"] == "candidate"
    assert contract["release_state"] == {
        "official_source_version": CURRENT_OFFICIAL_VERSION,
        "source_status": "candidate",
        "previous_official_version": CURRENT_OFFICIAL_VERSION,
        "github_release_requires_live_readback": True,
        "fleet_applied": False,
    }
    assert contract["review_gate"] == {
        "policy": "trusted-xiaomu-single-review",
        "exact_head_required": True,
        "status": "pending",
    }
    assert contract["authorization"] == {
        "merge_authorized": False,
        "tag_authorized": False,
        "release_authorized": False,
        "production_activation_authorized": False,
        "fleet_apply_authorized": False,
    }


def test_runtime_recovery_markers_are_ignored_and_not_tracked():
    ignored = {
        line.strip()
        for line in (ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    }
    assert ".lazy-refresh-incomplete" in ignored

    tracked = subprocess.run(
        ["git", "ls-files", "--error-unmatch", ".lazy-refresh-incomplete"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert tracked.returncode != 0, tracked.stdout


def test_required_tests_and_upstream_blobs_are_locked():
    verification = _load_contract()["verification"]
    assert set(verification) == {
        "required_test_files",
        "isolated_hermes_home_required",
        "upstream_preserved_blobs",
    }
    assert set(verification["required_test_files"]) == EXPECTED_REQUIRED_TESTS
    assert verification["isolated_hermes_home_required"] is True
    for relative_path in verification["required_test_files"]:
        assert (ROOT / relative_path).is_file(), relative_path

    assert verification["upstream_preserved_blobs"] == {
        "agent/lsp/manager.py": "7ba1b914f74c3728b97650ade147fa38d4c2bc53"
    }
    for relative_path, expected_blob in verification["upstream_preserved_blobs"].items():
        data = (ROOT / relative_path).read_bytes()
        actual = hashlib.sha1(
            f"blob {len(data)}\0".encode("ascii") + data,
            usedforsecurity=False,
        ).hexdigest()
        assert actual == expected_blob, relative_path


def test_contributor_check_uses_the_pull_request_base_branch():
    ci_workflow = (ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")
    contributor_workflow = (ROOT / ".github/workflows/contributor-check.yml").read_text(encoding="utf-8")

    assert (
        "if: needs.detect.outputs.python == 'true' && "
        "needs.detect.outputs.event_name == 'pull_request'\n"
        "    uses: ./.github/workflows/contributor-check.yml"
    ) in ci_workflow
    assert "GITHUB_BASE_REF: ${{ github.base_ref }}" in contributor_workflow
    assert 'git merge-base "origin/${GITHUB_BASE_REF}" HEAD' in contributor_workflow
    assert "git merge-base origin/main HEAD" not in contributor_workflow


def test_asl_production_runs_post_merge_ci_and_supports_manual_dispatch():
    ci_workflow = (ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")
    trigger_block = ci_workflow.split("\npermissions:", maxsplit=1)[0]

    assert "  push:\n    branches: [main, asl/production]" in trigger_block
    assert "  workflow_dispatch:" in trigger_block
