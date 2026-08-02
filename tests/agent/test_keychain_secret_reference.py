"""ASL successor tests for strict Keychain-backed credential references."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from agent import keychain_secret
from agent.credential_pool import (
    AUTH_TYPE_API_KEY,
    CredentialPool,
    KeychainReferenceUnresolved,
    PooledCredential,
    SOURCE_KEYCHAIN_REFERENCE,
    STATUS_EXHAUSTED,
    _prune_stale_seeded_entries,
)
from agent.error_classifier import FailoverReason


@pytest.mark.parametrize(
    "uri,service,account",
    [
        ("keychain://asl-hermes-relay/default", "asl-hermes-relay", "default"),
        ("keychain://svc_1/account.2", "svc_1", "account.2"),
    ],
)
def test_parse_keychain_uri_accepts_strict_reference(uri, service, account):
    assert keychain_secret.parse_keychain_uri(uri) == keychain_secret.KeychainRef(
        service=service,
        account=account,
    )


@pytest.mark.parametrize(
    "uri",
    [
        "",
        "file://svc/account",
        "keychain://svc",
        "keychain:///account",
        "keychain://svc/",
        "keychain://svc/account/extra",
        "keychain://svc/account?query=1",
        "keychain://svc/account#fragment",
        "keychain://svc/%61ccount",
        "keychain://svc with space/account",
        "keychain://svc/account\nnext",
    ],
)
def test_parse_keychain_uri_rejects_ambiguous_or_escaped_reference(uri):
    with pytest.raises(ValueError):
        keychain_secret.parse_keychain_uri(uri)


class _MemoryBackend:
    def __init__(self):
        self.values = {}

    def read(self, ref):
        try:
            return self.values[(ref.service, ref.account)]
        except KeyError:
            raise keychain_secret.KeychainNotFound(
                service=ref.service,
                account=ref.account,
            ) from None

    def write(self, ref, secret):
        self.values[(ref.service, ref.account)] = secret

    def delete(self, ref):
        if (ref.service, ref.account) not in self.values:
            raise keychain_secret.KeychainNotFound(
                service=ref.service,
                account=ref.account,
            )
        del self.values[(ref.service, ref.account)]


def test_public_keychain_api_uses_injected_backend_without_subprocess_or_files():
    backend = _MemoryBackend()
    ref = keychain_secret.parse_keychain_uri("keychain://svc/account")

    keychain_secret.write_keychain_secret(ref, "fixture-runtime-secret", backend=backend)
    assert keychain_secret.read_keychain_secret(ref, backend=backend) == "fixture-runtime-secret"
    keychain_secret.delete_keychain_secret(ref, backend=backend)

    with pytest.raises(keychain_secret.KeychainNotFound):
        keychain_secret.read_keychain_secret(ref, backend=backend)


@pytest.mark.parametrize("system", ["Linux", "Windows"])
def test_default_backend_fails_closed_off_darwin(system):
    with patch("agent.keychain_secret.platform.system", return_value=system):
        with pytest.raises(keychain_secret.KeychainUnavailable) as exc_info:
            keychain_secret.read_keychain_secret(
                keychain_secret.KeychainRef("svc", "account")
            )
    assert "fall back" in str(exc_info.value).lower()


def _reference_credential(
    *,
    credential_id="ref-1",
    priority=0,
    source=SOURCE_KEYCHAIN_REFERENCE,
    secret_source="keychain://svc/account",
    secret_fingerprint="sha256:fixture",
):
    return PooledCredential(
        provider="custom",
        id=credential_id,
        label="relay",
        auth_type=AUTH_TYPE_API_KEY,
        priority=priority,
        source=source,
        access_token="",
        base_url="https://relay.example.invalid/v1",
        extra={
            "secret_source": secret_source,
            "secret_fingerprint": secret_fingerprint,
        },
    )


def _manual_credential(*, credential_id="manual-1", priority=1):
    return PooledCredential(
        provider="custom",
        id=credential_id,
        label="manual fallback",
        auth_type=AUTH_TYPE_API_KEY,
        priority=priority,
        source="manual",
        access_token="healthy-runtime-secret",
        base_url="https://relay.example.invalid/v1",
    )


def test_keychain_reference_resolves_only_at_runtime_and_never_serializes_secret(monkeypatch):
    credential = _reference_credential()
    calls = []

    def fake_read(ref):
        calls.append(ref)
        return "fixture-runtime-secret"

    monkeypatch.setattr(keychain_secret, "read_keychain_secret", fake_read)

    assert credential.runtime_api_key == "fixture-runtime-secret"
    assert credential.runtime_api_key == "fixture-runtime-secret"
    assert len(calls) == 2  # no in-process secret cache

    persisted = credential.to_dict()
    assert persisted["secret_source"] == "keychain://svc/account"
    assert persisted["secret_fingerprint"] == "sha256:fixture"
    assert "fixture-runtime-secret" not in repr(persisted)
    assert persisted.get("access_token", "") == ""


def test_keychain_reference_resolution_error_is_categorized_without_secret(monkeypatch):
    credential = _reference_credential()

    def fail(_ref):
        raise keychain_secret.KeychainError(
            service="svc",
            account="account",
            category="interaction_not_allowed",
            os_status=-25308,
        )

    monkeypatch.setattr(keychain_secret, "read_keychain_secret", fail)

    with pytest.raises(KeychainReferenceUnresolved) as exc_info:
        _ = credential.runtime_api_key

    assert exc_info.value.category == "interaction_not_allowed"
    assert "fixture-runtime-secret" not in str(exc_info.value)
    assert "keychain://" not in str(exc_info.value)


def test_keychain_error_string_does_not_leak_service_or_account():
    error = keychain_secret.KeychainError(
        service="fixture-sensitive-service",
        account="fixture-sensitive-account",
        category="interaction_not_allowed",
        os_status=-25308,
    )

    rendered = str(error)
    assert "fixture-sensitive-service" not in rendered
    assert "fixture-sensitive-account" not in rendered
    assert "interaction_not_allowed" in rendered
    assert "-25308" in rendered


def test_keychain_reference_survives_ordinary_stale_seed_pruning():
    entries = [_reference_credential()]
    changed = _prune_stale_seeded_entries(entries, active_sources=set())
    assert changed is False
    assert [entry.id for entry in entries] == ["ref-1"]


def test_disabled_legacy_flag_round_trips_as_metadata():
    credential = PooledCredential.from_dict(
        "custom",
        {
            "id": "legacy-1",
            "label": "disabled legacy",
            "auth_type": "api_key",
            "priority": 1,
            "source": "manual",
            "access_token": "legacy-must-not-be-selected",
            "disabled": True,
        },
    )
    assert credential.extra["disabled"] is True
    assert credential.to_dict()["disabled"] is True


def test_invalid_keychain_reference_fails_closed_without_using_access_token():
    credential = _reference_credential(secret_source="keychain://bad/too/many")
    credential.access_token = "legacy-must-not-be-used"

    with pytest.raises(KeychainReferenceUnresolved) as exc_info:
        _ = credential.runtime_api_key

    assert exc_info.value.category == "invalid_secret_source"
    assert "legacy-must-not-be-used" not in str(exc_info.value)


def test_stable_id_failure_marks_keychain_fingerprint_siblings_without_secret_read(
    tmp_path, monkeypatch
):
    """A known failed entry is quarantined by stable metadata, not Keychain I/O."""
    hermes_home = tmp_path / "hermes"
    hermes_home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(hermes_home))
    pool = CredentialPool(
        "custom",
        [
            _reference_credential(credential_id="ref-failed", priority=0),
            _reference_credential(
                credential_id="ref-sibling",
                priority=1,
                secret_source="keychain://svc/sibling",
            ),
            _manual_credential(priority=2),
        ],
    )

    def keychain_locked(_ref):
        raise keychain_secret.KeychainError(
            category="interaction_not_allowed",
            os_status=-25308,
        )

    keychain_reads = MagicMock(side_effect=keychain_locked)
    monkeypatch.setattr(keychain_secret, "read_keychain_secret", keychain_reads)

    next_entry = pool.mark_exhausted_and_rotate(
        status_code=402,
        api_key_hint="runtime-secret-used-before-keychain-locked",
        credential_id="ref-failed",
    )

    assert next_entry is not None
    assert next_entry.id == "manual-1"
    assert {
        entry.id: entry.last_status for entry in pool.entries()
    } == {
        "ref-failed": STATUS_EXHAUSTED,
        "ref-sibling": STATUS_EXHAUSTED,
        "manual-1": None,
    }
    keychain_reads.assert_not_called()


def test_sibling_matching_skips_one_unresolved_keychain_reference(
    tmp_path, monkeypatch
):
    """One unavailable sibling cannot abort rotation away from a failed key."""
    hermes_home = tmp_path / "hermes"
    hermes_home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(hermes_home))
    failed = _manual_credential(credential_id="manual-failed", priority=0)
    failed.access_token = "failed-runtime-secret"
    pool = CredentialPool(
        "custom",
        [
            failed,
            _reference_credential(
                credential_id="ref-unavailable",
                priority=1,
                secret_fingerprint="sha256:different-secret",
            ),
            _manual_credential(credential_id="manual-healthy", priority=2),
        ],
    )

    def keychain_locked(_ref):
        raise keychain_secret.KeychainError(
            category="interaction_not_allowed",
            os_status=-25308,
        )

    monkeypatch.setattr(keychain_secret, "read_keychain_secret", keychain_locked)

    next_entry = pool.mark_exhausted_and_rotate(
        status_code=429,
        api_key_hint="failed-runtime-secret",
        credential_id="manual-failed",
    )

    assert next_entry is not None
    assert next_entry.id == "manual-healthy"
    assert {
        entry.id: entry.last_status for entry in pool.entries()
    } == {
        "manual-failed": STATUS_EXHAUSTED,
        "ref-unavailable": None,
        "manual-healthy": None,
    }


@pytest.mark.parametrize(
    ("reason", "status_code", "has_retried_429"),
    [
        (FailoverReason.billing, 402, False),
        (FailoverReason.auth, 401, False),
        (FailoverReason.rate_limit, 429, True),
    ],
)
def test_runtime_recovery_uses_stable_id_after_keychain_becomes_unavailable(
    tmp_path,
    monkeypatch,
    reason,
    status_code,
    has_retried_429,
):
    """Real 402/401/429 callers keep rotating after post-selection Keychain lock."""
    hermes_home = tmp_path / "hermes"
    hermes_home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(hermes_home))
    reference = _reference_credential(credential_id="ref-failed")
    pool = CredentialPool("custom", [reference, _manual_credential()])

    monkeypatch.setattr(
        keychain_secret,
        "read_keychain_secret",
        lambda _ref: "runtime-secret-used-for-successful-selection",
    )
    selected = pool.select()
    assert selected is not None
    assert selected.id == "ref-failed"
    runtime_secret = selected.runtime_api_key

    def keychain_locked(_ref):
        raise keychain_secret.KeychainError(
            category="interaction_not_allowed",
            os_status=-25308,
        )

    keychain_reads = MagicMock(side_effect=keychain_locked)
    monkeypatch.setattr(keychain_secret, "read_keychain_secret", keychain_reads)
    agent = SimpleNamespace(
        provider="custom",
        api_key=runtime_secret,
        _credential_pool=pool,
        _credential_pool_entry_id=selected.id,
        _swap_credential=MagicMock(),
        _is_entitlement_failure=MagicMock(return_value=False),
    )

    from agent.agent_runtime_helpers import recover_with_credential_pool

    recovered, retried_429 = recover_with_credential_pool(
        agent,
        status_code=status_code,
        has_retried_429=has_retried_429,
        classified_reason=reason,
        error_context={"reason": "fixture-provider-failure"},
    )

    assert recovered is True
    assert retried_429 is False
    assert pool.entries()[0].last_status == STATUS_EXHAUSTED
    assert agent._swap_credential.call_args.args[0].id == "manual-1"
    keychain_reads.assert_not_called()
