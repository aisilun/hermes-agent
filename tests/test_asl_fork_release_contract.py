"""Machine gates for the official ASL-maintained Hermes fork release."""

from __future__ import annotations

import json
import re
import subprocess
import tomllib
from pathlib import Path
from types import SimpleNamespace


ROOT = Path(__file__).resolve().parents[1]
CONTRACT_PATH = ROOT / "governance" / "asl-fork-release.json"
EXPECTED_VERSION = "0.19.0+asl.4"
EXPECTED_TAG = "v0.19.0-asl.4"
CURRENT_OFFICIAL_VERSION = "0.19.0+asl.3"
EXPECTED_REQUIRED_TESTS = {
    "tests/agent/test_turn_gate.py",
    "tests/agent/test_conversation_reload_gate.py",
    "tests/agent/test_tool_executor_reload_gate.py",
    "tests/agent/test_host_tool_env_bridge.py",
    "tests/gateway/test_reload_turn_gate.py",
    "tests/hermes_cli/test_turn_gate_plugin.py",
    "tests/hermes_cli/test_kanban_privileged_delegation.py",
    "tests/test_asl_fork_release_contract.py",
    "tests/hermes_cli/test_banner_git_state.py",
    "tests/hermes_cli/test_cmd_update.py",
    "tests/hermes_cli/test_update_zip_symlink_reject.py",
}


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
        "distribution",
        "verification",
        "authorization",
    }
    assert contract["schema_version"] == 1

    candidate = contract["candidate"]
    assert candidate == {
        "repository": "aisilun/hermes-agent",
        "package_version": EXPECTED_VERSION,
        "planned_tag": EXPECTED_TAG,
        "status": "candidate",
        "prepared_at": "2026-07-31",
    }

    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    assert project["version"] == EXPECTED_VERSION

    init_text = (ROOT / "hermes_cli" / "__init__.py").read_text(encoding="utf-8")
    assert f'__version__ = "{EXPECTED_VERSION}"' in init_text
    assert '__release_date__ = "2026.7.31"' in init_text


def test_asl_fork_contract_binds_source_and_explicit_divergence():
    source = _load_contract()["source"]
    assert set(source) == {
        "upstream_repository",
        "official_release_tag",
        "official_release_commit",
        "upstream_base_commit",
        "turn_gate_source_commit",
        "upstream_pull_request",
        "latest_upstream_main_observed",
    }
    assert source["upstream_repository"] == "NousResearch/hermes-agent"
    assert source["official_release_tag"] == "v2026.7.20"
    assert source["official_release_commit"] == "3ef6bbd201263d354fd83ec55b3c306ded2eb72a"
    assert source["upstream_base_commit"] == "0bd82a8a84595720ea1f14b103aeb81ca3cc50ef"
    assert source["turn_gate_source_commit"] == "0e1031a9ff05d0c0d2f44f2148b80a33ca9d3561"
    assert source["upstream_pull_request"] == "https://github.com/NousResearch/hermes-agent/pull/74529"
    assert source["latest_upstream_main_observed"] == {
        "commit": "cc4cab2f592e60a197e796506de9168f74baf3ea",
        "observed_at": "2026-07-31",
        "included": False,
    }
    for key in ("official_release_commit", "upstream_base_commit", "turn_gate_source_commit"):
        assert re.fullmatch(r"[0-9a-f]{40}", source[key])


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


def test_asl_fork_contract_keeps_release_and_activation_closed():
    contract = _load_contract()
    assert contract["maintenance"] == {
        "owner": "aisilun",
        "upstream_tracking": "NousResearch/hermes-agent#74529",
        "reconciliation_policy": "explicit-tested-port-only",
    }


def test_asl_fork_candidate_keeps_release_and_live_activation_closed():
    contract = _load_contract()

    assert contract["candidate"]["status"] == "candidate"
    assert contract["release_state"] == {
        "official_source_version": CURRENT_OFFICIAL_VERSION,
        "source_status": "candidate",
        "previous_official_version": CURRENT_OFFICIAL_VERSION,
        "github_release_requires_live_readback": True,
        "fleet_applied": False,
    }
    assert contract["authorization"] == {
        "merge_authorized": True,
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


def test_asl_fork_contract_names_existing_required_tests():
    verification = _load_contract()["verification"]
    assert set(verification) == {"required_test_files", "isolated_hermes_home_required"}
    assert set(verification["required_test_files"]) == EXPECTED_REQUIRED_TESTS
    assert verification["isolated_hermes_home_required"] is True
    for relative_path in verification["required_test_files"]:
        assert (ROOT / relative_path).is_file(), relative_path


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
