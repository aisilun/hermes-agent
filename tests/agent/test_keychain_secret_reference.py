"""ASL successor tests for strict Keychain-backed credential references."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from agent import keychain_secret
from agent.credential_pool import (
    AUTH_TYPE_API_KEY,
    KeychainReferenceUnresolved,
    PooledCredential,
    SOURCE_KEYCHAIN_REFERENCE,
    _prune_stale_seeded_entries,
)


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


def _reference_credential(*, source=SOURCE_KEYCHAIN_REFERENCE, secret_source="keychain://svc/account"):
    return PooledCredential(
        provider="custom",
        id="ref-1",
        label="relay",
        auth_type=AUTH_TYPE_API_KEY,
        priority=0,
        source=source,
        access_token="",
        base_url="https://relay.example.invalid/v1",
        extra={
            "secret_source": secret_source,
            "secret_fingerprint": "sha256:fixture",
        },
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
