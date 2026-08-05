"""Machine gates for the ASL Hermes v0.20.0 thin-overlay release."""

from __future__ import annotations

import json
import re
import tomllib
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
CONTRACT_PATH = ROOT / "governance" / "asl-fork-release.json"
RELEASE_DOC = ROOT / "ASL_FORK.md"
EXPECTED_VERSION = "0.20.0"
EXPECTED_TAG = "v0.20.0-asl.1"
UPSTREAM_TAG = "v2026.8.3"
UPSTREAM_TAG_OBJECT = "7de39e700d2c329e15d32eb0b96e2f7cdd9fbdb2"
UPSTREAM_COMMIT = "3c27eb6234bf91b8ceee9e9071591b31e9b148cb"
UPSTREAM_TREE = "b217767ccb994605dad522e693fa1b4cdbc2f352"
SOURCE_PR = 12
SOURCE_REVIEWED_HEAD = "70a2fd89eb9c9da594968c1d552eaf918a82bda1"
SOURCE_REVIEW_ID = 4867149067
SOURCE_MERGE_COMMIT = "c0872e8b27bf4232dc353d6a0d43d6a132033744"
SOURCE_MERGE_TREE = "e0a5957f695448ec971abfc9505d794aab3dc53b"
SOURCE_CI_RUN = 31010701357


def load_contract() -> dict:
    return json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))


def test_release_contract_files_exist() -> None:
    assert RELEASE_DOC.is_file()
    assert CONTRACT_PATH.is_file()


def test_official_upstream_and_thin_overlay_are_exactly_bound() -> None:
    contract = load_contract()
    assert contract["schema_version"] == 1
    assert contract["upstream"] == {
        "repository": "NousResearch/hermes-agent",
        "version": EXPECTED_VERSION,
        "tag": UPSTREAM_TAG,
        "tag_type": "annotated",
        "tag_object": UPSTREAM_TAG_OBJECT,
        "commit": UPSTREAM_COMMIT,
        "tree": UPSTREAM_TREE,
    }
    assert contract["overlay"]["repository"] == "aisilun/hermes-agent"
    assert contract["overlay"]["base_branch"] == "asl/upstream-v2026.8.3"
    assert contract["overlay"]["source_pr"] == SOURCE_PR
    assert contract["overlay"]["source_reviewed_head"] == SOURCE_REVIEWED_HEAD
    assert contract["overlay"]["source_review_id"] == SOURCE_REVIEW_ID
    assert contract["overlay"]["source_review_state"] == "APPROVED"
    assert contract["overlay"]["source_merge_commit"] == SOURCE_MERGE_COMMIT
    assert contract["overlay"]["source_merge_tree"] == SOURCE_MERGE_TREE
    assert contract["overlay"]["source_pr_tree"] == SOURCE_MERGE_TREE
    assert contract["overlay"]["policy_model"] == "policy-neutral-host-api"
    assert contract["overlay"]["retirement_condition"] == (
        "upstream-equivalent-release-verified"
    )
    for field in ("source_reviewed_head", "source_merge_commit", "source_merge_tree"):
        assert re.fullmatch(r"[0-9a-f]{40}", contract["overlay"][field])


def test_release_state_is_official_source_ready_without_activation() -> None:
    contract = load_contract()
    assert contract["release"] == {
        "package_version": EXPECTED_VERSION,
        "release_version": "0.20.0-asl.1",
        "planned_tag": EXPECTED_TAG,
        "release_name": "ASL Hermes v0.20.0+asl.1 Host Gate thin overlay",
        "status": "official-source-ready",
        "release_only_pr": "pending",
        "release_reviewed_head": "pending",
        "release_review_id": "pending",
        "release_merge_commit": "pending",
        "tag_created": False,
        "github_release_created": False,
        "asset_count": 0,
    }
    assert contract["authorization"] == {
        "merge_authorized": True,
        "tag_authorized": True,
        "release_authorized": True,
        "production_activation_authorized": False,
        "gateway_restart_authorized": False,
        "profile_apply_authorized": False,
        "fleet_apply_authorized": False,
        "database_migration_authorized": False,
    }


def test_distribution_is_source_checkout_and_does_not_rebrand_upstream_version() -> None:
    contract = load_contract()
    assert contract["distribution"] == {
        "method": "shell-installer-source-checkout",
        "repository": "https://github.com/aisilun/hermes-agent.git",
        "ref": EXPECTED_TAG,
        "release_assets": [],
        "unsupported_artifacts": ["wheel", "sdist", "pypi"],
    }
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    assert project["version"] == EXPECTED_VERSION
    init_text = (ROOT / "hermes_cli" / "__init__.py").read_text(encoding="utf-8")
    assert '__version__ = "0.20.0"' in init_text
    assert '__release_date__ = "2026.8.3"' in init_text
    assert "0.20.0+asl.1" not in (ROOT / "pyproject.toml").read_text(encoding="utf-8")


def test_verification_contract_binds_source_ci_and_postmerge_replay() -> None:
    verification = load_contract()["verification"]
    assert verification["source_pr_ci_run"] == SOURCE_CI_RUN
    assert verification["source_pr_check_runs_total"] == 37
    assert verification["source_pr_check_runs_success"] == 25
    assert verification["source_pr_check_runs_skipped"] == 12
    assert verification["source_pr_check_runs_failed"] == 0
    assert verification["source_author_self_check"] == "150 passed"
    assert verification["source_post_merge_ci"] == "CI_NOT_CONFIGURED"
    assert verification["release_only_exact_head_ci_required"] is True
    assert verification["release_merge_workflow_dispatch_required"] is False
    assert verification["release_merge_detached_verification_required"] is True
    assert verification["required_host_api_paths"] == [
        "agent/turn_gate.py",
        "hermes_cli/plugins.py",
        "tools/environments/local.py",
    ]
    for relative in verification["required_host_api_paths"]:
        assert (ROOT / relative).is_file(), relative


def test_release_document_binds_same_identity_and_forbidden_boundaries() -> None:
    text = RELEASE_DOC.read_text(encoding="utf-8")
    for required in (
        "0.20.0-asl.1",
        UPSTREAM_TAG,
        UPSTREAM_COMMIT,
        str(SOURCE_PR),
        SOURCE_REVIEWED_HEAD,
        str(SOURCE_REVIEW_ID),
        SOURCE_MERGE_COMMIT,
        SOURCE_MERGE_TREE,
        "release_only_pr=pending",
        "tag_created=false",
        "github_release_created=false",
        "production_activation_authorized=false",
        "gateway_restart_authorized=false",
        "profile_apply_authorized=false",
        "fleet_apply_authorized=false",
        "database_migration_authorized=false",
    ):
        assert required in text


def test_contract_rejects_future_object_fabrication() -> None:
    release = load_contract()["release"]
    for key in (
        "release_only_pr",
        "release_reviewed_head",
        "release_review_id",
        "release_merge_commit",
    ):
        assert release[key] == "pending"
    assert release["tag_created"] is False
    assert release["github_release_created"] is False


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__]))
