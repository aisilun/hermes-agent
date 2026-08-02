"""Contract tests for the ASL-bundled GrsAI image provider."""

from __future__ import annotations

import importlib
from pathlib import Path
from types import SimpleNamespace

import yaml


PLUGIN_DIR = Path(__file__).resolve().parents[2] / "plugins" / "image_gen" / "grsai"


def _module():
    return importlib.import_module("plugins.image_gen.grsai")


def test_grsai_manifest_is_bundled_backend():
    data = yaml.safe_load((PLUGIN_DIR / "plugin.yaml").read_text(encoding="utf-8"))
    assert data["name"] == "grsai"
    assert data["kind"] == "backend"
    assert data["requires_env"] == ["GRSAI_API_KEY"]


def test_grsai_registers_image_provider():
    mod = _module()
    registered = []
    mod.register(SimpleNamespace(register_image_gen_provider=registered.append))
    assert len(registered) == 1
    assert registered[0].name == "grsai"
    assert registered[0].display_name == "GrsAI"
    assert registered[0].default_model() == "gpt-image-2-vip"


def test_grsai_submit_poll_success_contract(monkeypatch):
    mod = _module()
    monkeypatch.setattr(mod, "_api_key", lambda: "fixture-key")
    monkeypatch.setattr(mod.time, "sleep", lambda _seconds: None)
    calls = []

    def fake_post(path, payload, *, timeout):
        calls.append((path, payload, timeout))
        if path == "/v1/draw/completions":
            return {"code": 0, "data": {"id": "task-1"}}
        assert path == "/v1/draw/result"
        return {
            "code": 0,
            "data": {
                "status": "succeeded",
                "progress": 100,
                "results": [{"url": "https://images.example.invalid/out.png"}],
            },
        }

    monkeypatch.setattr(mod, "_post_json", fake_post)
    result = mod.GrsAIImageGenProvider().generate(
        "a fixture product photo",
        aspect_ratio="landscape",
        model="gpt-image-2-vip",
    )

    assert result["success"] is True
    assert result["provider"] == "grsai"
    assert result["model"] == "gpt-image-2-vip"
    assert result["image"] == "https://images.example.invalid/out.png"
    assert calls[0][0] == "/v1/draw/completions"
    assert calls[0][1]["aspectRatio"] == "3840x2160"
    assert calls[0][1]["webHook"] == "-1"
    assert calls[1][0] == "/v1/draw/result"
    assert calls[1][1] == {"id": "task-1"}


def test_grsai_auth_required_without_key(monkeypatch):
    mod = _module()
    monkeypatch.setattr(mod, "_api_key", lambda: "")
    result = mod.GrsAIImageGenProvider().generate("fixture prompt")
    assert result["success"] is False
    assert result["error_type"] == "auth_required"
    assert result["provider"] == "grsai"


def test_grsai_invalid_prompt_fails_before_network(monkeypatch):
    mod = _module()
    monkeypatch.setattr(mod, "_api_key", lambda: "fixture-key")
    monkeypatch.setattr(
        mod,
        "_post_json",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("network called")),
    )
    result = mod.GrsAIImageGenProvider().generate("   ")
    assert result["success"] is False
    assert result["error_type"] == "invalid_argument"


def test_grsai_error_never_echoes_authorization_secret(monkeypatch):
    mod = _module()
    secret = "fixture-super-secret-value"
    monkeypatch.setattr(mod, "_api_key", lambda: secret)

    def fail(*_args, **_kwargs):
        header = "Authori" + "zation"
        scheme = "Bear" + "er"
        raise RuntimeError(f"{header}: {scheme} {secret}")

    monkeypatch.setattr(mod, "_post_json", fail)
    result = mod.GrsAIImageGenProvider().generate("fixture prompt")
    assert result["success"] is False
    assert secret not in result["error"]
    assert "Authorization" in result["error"]
    assert "Bearer" in result["error"]


def test_grsai_reads_existing_vault_compatibly_without_writing(tmp_path, monkeypatch):
    mod = _module()
    from cryptography.fernet import Fernet

    monkeypatch.setattr(mod, "_hermes_home", lambda: tmp_path)
    key = Fernet.generate_key()
    monkeypatch.setattr(mod, "_vault_key", lambda: key)
    secret_dir = tmp_path / "secrets"
    secret_dir.mkdir()
    vault = secret_dir / mod.VAULT_FILE
    token = Fernet(key).encrypt(b"fixture-vault-secret").decode("ascii")
    vault.write_text('{"token":"' + token + '"}', encoding="utf-8")
    before = vault.read_bytes()

    assert mod._api_key_from_vault() == "fixture-vault-secret"
    assert vault.read_bytes() == before
