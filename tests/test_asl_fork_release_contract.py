"""Machine gates for the ASL-maintained Hermes fork release candidate."""

from __future__ import annotations

import json
import re
import tomllib
from pathlib import Path
from types import SimpleNamespace


ROOT = Path(__file__).resolve().parents[1]
CONTRACT_PATH = ROOT / "governance" / "asl-fork-release.json"
EXPECTED_VERSION = "0.19.0+asl.1"
EXPECTED_TAG = "v0.19.0-asl.1"
EXPECTED_REQUIRED_TESTS = {
    "tests/agent/test_turn_gate.py",
    "tests/agent/test_conversation_reload_gate.py",
    "tests/agent/test_tool_executor_reload_gate.py",
    "tests/agent/test_host_tool_env_bridge.py",
    "tests/gateway/test_reload_turn_gate.py",
    "tests/hermes_cli/test_turn_gate_plugin.py",
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
        "status": "official",
        "prepared_at": "2026-07-30",
    }

    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    assert project["version"] == EXPECTED_VERSION

    init_text = (ROOT / "hermes_cli" / "__init__.py").read_text(encoding="utf-8")
    assert f'__version__ = "{EXPECTED_VERSION}"' in init_text
    assert '__release_date__ = "2026.7.30"' in init_text


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
        "commit": "937222f4ec80e6991e934e0b140b60e0030c55fd",
        "observed_at": "2026-07-30",
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
        "planned_ref": "v0.19.0-asl.1",
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


def test_asl_fork_release_state_is_official_and_live_activation_stays_closed():
    contract = _load_contract()

    assert contract["candidate"]["status"] == "official"
    assert contract["release_state"] == {
        "official_source_version": EXPECTED_VERSION,
        "source_status": "official",
        "previous_official_version": None,
        "github_release_requires_live_readback": True,
        "fleet_applied": False,
    }
    assert contract["authorization"] == {
        "merge_authorized": True,
        "tag_authorized": True,
        "release_authorized": True,
        "production_activation_authorized": False,
        "fleet_apply_authorized": False,
    }


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
