"""GrsAI image generation backend.

Implements the GPT Image API documented at:
https://grsai.ai/zh/dashboard/documents/gpt-image

Endpoint shape:
- POST {base_url}/v1/draw/completions with Authorization: Bearer <key>
- POST {base_url}/v1/draw/result for polling when webHook is "-1"
"""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import os
import platform
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Optional

from agent.image_gen_provider import (
    DEFAULT_ASPECT_RATIO,
    ImageGenProvider,
    error_response,
    resolve_aspect_ratio,
    success_response,
)
from agent.redact import redact_log_text

logger = logging.getLogger(__name__)

DEFAULT_BASE_URL = "https://grsaiapi.com"
DEFAULT_MODEL = "gpt-image-2-vip"
VAULT_FILE = "grsai_api_key.vault.json"

# GrsAI's GPT Image endpoint expects literal pixel sizes in `aspectRatio`.
# Defaults below intentionally choose the highest-resolution VIP sizes from
# https://grsai.ai/zh/dashboard/documents/gpt-image for production-quality
# ecommerce imagery.
_ASPECT_MAP_BY_MODEL = {
    "gpt-image-2": {
        "landscape": "1536x1024",
        "square": "1024x1024",
        "portrait": "1024x1536",
    },
    "gpt-image-2-vip": {
        "landscape": "3840x2160",
        "square": "2880x2880",
        "portrait": "2160x3840",
    },
}


def _hermes_home() -> Path:
    try:
        from hermes_constants import get_hermes_home

        return Path(get_hermes_home())
    except Exception:
        return Path(os.path.expanduser("~/.hermes"))


def _hardware_fingerprint() -> str:
    """Return a stable local-machine fingerprint for the encrypted vault key."""
    parts = [platform.system(), platform.node(), str(_hermes_home())]
    if platform.system() == "Darwin":
        try:
            out = subprocess.run(
                ["ioreg", "-rd1", "-c", "IOPlatformExpertDevice"],
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True,
                timeout=2,
            ).stdout
            for line in out.splitlines():
                if "IOPlatformUUID" in line:
                    parts.append(line.split("=", 1)[1].strip().strip('"'))
                    break
        except Exception:
            pass
    return "|".join(p for p in parts if p)


def _vault_key() -> bytes:
    # Fernet requires a URL-safe base64-encoded 32-byte key.
    digest = hashlib.sha256(("hermes-grsai-vault-v1|" + _hardware_fingerprint()).encode("utf-8")).digest()
    return base64.urlsafe_b64encode(digest)


def _api_key_from_vault() -> str:
    path = _hermes_home() / "secrets" / VAULT_FILE
    if not path.exists():
        return ""
    try:
        from cryptography.fernet import Fernet

        payload = json.loads(path.read_text(encoding="utf-8"))
        token = payload.get("token", "")
        if not token:
            return ""
        return Fernet(_vault_key()).decrypt(token.encode("utf-8")).decode("utf-8").strip()
    except Exception as exc:
        logger.debug(
            "Could not read GrsAI API key vault (%s)", type(exc).__name__
        )
        return ""


def _api_key() -> str:
    # Never borrow OPENAI_API_KEY for a different egress provider. A GrsAI
    # request must be authorized explicitly or by the existing read-only vault.
    return (os.environ.get("GRSAI_API_KEY") or _api_key_from_vault() or "").strip()


def _safe_external_error(value: object) -> str:
    try:
        return redact_log_text(value)
    except Exception:
        return "[REDACTED - GrsAI error sanitization failed]"


def _base_url() -> str:
    return (os.environ.get("GRSAI_BASE_URL") or DEFAULT_BASE_URL).strip().rstrip("/")


def _model(configured: Optional[str] = None) -> str:
    """Resolve the GrsAI model, allowing config/env override."""
    value = (configured or os.environ.get("GRSAI_IMAGE_MODEL") or DEFAULT_MODEL).strip()
    return value if value in _ASPECT_MAP_BY_MODEL else DEFAULT_MODEL


def _aspect_ratio_for_model(model: str, aspect: str) -> str:
    return _ASPECT_MAP_BY_MODEL.get(model, _ASPECT_MAP_BY_MODEL[DEFAULT_MODEL]).get(
        aspect,
        _ASPECT_MAP_BY_MODEL[DEFAULT_MODEL]["square"],
    )


def _post_json(path: str, payload: Dict[str, Any], *, timeout: int = 60) -> Dict[str, Any]:
    key = _api_key()
    if not key:
        raise RuntimeError("GRSAI_API_KEY not set")

    url = f"{_base_url()}{path}"
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=body,
        method="POST",
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {key}",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")
        raise RuntimeError(
            f"HTTP {exc.code}: {_safe_external_error(detail[:500])}"
        ) from None

    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        # Defensive fallback for stream/SSE-like responses. Keep only parsed JSON chunks.
        last: Optional[Dict[str, Any]] = None
        for line in raw.splitlines():
            line = line.strip()
            if not line or line == "data: [DONE]":
                continue
            if line.startswith("data:"):
                line = line[5:].strip()
            try:
                obj = json.loads(line)
                if isinstance(obj, dict):
                    last = obj
            except Exception:
                continue
        if last is not None:
            return last
        raise RuntimeError(
            f"Non-JSON response from GrsAI: {_safe_external_error(raw[:500])}"
        )


def _extract_task_id(resp: Dict[str, Any]) -> Optional[str]:
    data = resp.get("data") if isinstance(resp, dict) else None
    if isinstance(data, dict):
        task_id = data.get("id")
        if isinstance(task_id, str) and task_id:
            return task_id
    task_id = resp.get("id") if isinstance(resp, dict) else None
    return task_id if isinstance(task_id, str) and task_id else None


def _extract_result(obj: Dict[str, Any]) -> Dict[str, Any]:
    # /v1/draw/result wraps result in data; stream/webhook may return result directly.
    data = obj.get("data") if isinstance(obj.get("data"), dict) else obj
    return data if isinstance(data, dict) else obj


class GrsAIImageGenProvider(ImageGenProvider):
    @property
    def name(self) -> str:
        return "grsai"

    @property
    def display_name(self) -> str:
        return "GrsAI"

    def is_available(self) -> bool:
        return bool(_api_key())

    def list_models(self) -> List[Dict[str, Any]]:
        return [
            {
                "id": "gpt-image-2",
                "display": "GrsAI GPT Image 2",
                "speed": "varies",
                "strengths": "GPT Image API via GrsAI relay; supports text-to-image and reference-image URLs",
                "price": "GrsAI credits",
            },
            {
                "id": "gpt-image-2-vip",
                "display": "GrsAI GPT Image 2 VIP",
                "speed": "varies",
                "strengths": "Highest-resolution GPT Image 2 via GrsAI relay; production ecommerce output",
                "price": "GrsAI credits",
            },
        ]

    def default_model(self) -> Optional[str]:
        return DEFAULT_MODEL

    def get_setup_schema(self) -> Dict[str, Any]:
        return {
            "name": "GrsAI",
            "badge": "paid",
            "tag": "GPT Image API via GrsAI /v1/draw/completions",
            "env_vars": [
                {
                    "key": "GRSAI_API_KEY",
                    "prompt": "GrsAI API key",
                    "url": "https://grsai.ai/zh/dashboard/api-keys",
                },
                {
                    "key": "GRSAI_BASE_URL",
                    "prompt": "GrsAI API host (optional, default https://grsaiapi.com)",
                    "url": "https://grsai.ai/zh/dashboard/documents/gpt-image",
                },
            ],
        }

    def generate(
        self,
        prompt: str,
        aspect_ratio: str = DEFAULT_ASPECT_RATIO,
        **kwargs: Any,
    ) -> Dict[str, Any]:
        prompt = (prompt or "").strip()
        model = _model(kwargs.get("model"))
        aspect = resolve_aspect_ratio(aspect_ratio)
        if not prompt:
            return error_response(
                error="Prompt is required and must be a non-empty string",
                error_type="invalid_argument",
                provider="grsai",
                model=model,
                aspect_ratio=aspect,
            )
        if not _api_key():
            return error_response(
                error="GRSAI_API_KEY not set",
                error_type="auth_required",
                provider="grsai",
                model=model,
                prompt=prompt,
                aspect_ratio=aspect,
            )

        payload: Dict[str, Any] = {
            "model": model,
            "prompt": prompt,
            "aspectRatio": _aspect_ratio_for_model(model, aspect),
            "urls": [],
            # Ask the API to immediately return an id, then poll /v1/draw/result.
            "webHook": "-1",
            "shutProgress": False,
        }
        quality = kwargs.get("quality") or os.environ.get("GRSAI_IMAGE_QUALITY") or "high"
        if quality:
            payload["quality"] = str(quality)

        try:
            submit = _post_json("/v1/draw/completions", payload, timeout=90)
            if submit.get("code") not in (None, 0):
                return error_response(
                    error=f"GrsAI submit failed: {_safe_external_error(submit)}",
                    error_type="api_error",
                    provider="grsai",
                    model=model,
                    prompt=prompt,
                    aspect_ratio=aspect,
                )
            task_id = _extract_task_id(submit)
            if not task_id:
                # Some modes may return the result directly.
                result = _extract_result(submit)
            else:
                result = {}
                deadline = time.time() + int(os.environ.get("GRSAI_IMAGE_TIMEOUT", "240"))
                while time.time() < deadline:
                    polled = _post_json("/v1/draw/result", {"id": task_id}, timeout=60)
                    if polled.get("code") not in (None, 0):
                        code = polled.get("code")
                        if code == -22:
                            time.sleep(3)
                            continue
                        return error_response(
                            error=f"GrsAI poll failed: {_safe_external_error(polled)}",
                            error_type="api_error",
                            provider="grsai",
                            model=model,
                            prompt=prompt,
                            aspect_ratio=aspect,
                        )
                    result = _extract_result(polled)
                    status = result.get("status")
                    progress = result.get("progress")
                    if status == "succeeded" or progress == 100:
                        break
                    if status == "failed":
                        return error_response(
                            error=(
                                "GrsAI generation failed: "
                                f"{_safe_external_error(result.get('failure_reason') or result.get('error') or result)}"
                            ),
                            error_type="generation_failed",
                            provider="grsai",
                            model=model,
                            prompt=prompt,
                            aspect_ratio=aspect,
                        )
                    time.sleep(5)
                else:
                    return error_response(
                        error=f"Timed out waiting for GrsAI image result; task_id={task_id}",
                        error_type="timeout",
                        provider="grsai",
                        model=model,
                        prompt=prompt,
                        aspect_ratio=aspect,
                    )

            results = result.get("results") if isinstance(result, dict) else None
            image_url = None
            if isinstance(results, list) and results:
                first = results[0]
                if isinstance(first, dict):
                    image_url = first.get("url")
            if not image_url and isinstance(result, dict):
                image_url = result.get("url")
            if not image_url:
                return error_response(
                    error=(
                        "GrsAI returned no image URL: "
                        f"{_safe_external_error(result)}"
                    ),
                    error_type="empty_response",
                    provider="grsai",
                    model=model,
                    prompt=prompt,
                    aspect_ratio=aspect,
                )

            return success_response(
                image=str(image_url),
                model=model,
                prompt=prompt,
                aspect_ratio=aspect,
                provider="grsai",
                extra={
                    "task_id": task_id if 'task_id' in locals() else None,
                    "aspectRatio": payload["aspectRatio"],
                    "raw_status": result.get("status") if isinstance(result, dict) else None,
                },
            )
        except Exception as exc:
            logger.debug(
                "GrsAI image generation failed (%s)", type(exc).__name__
            )
            return error_response(
                error=(
                    "GrsAI image generation failed: "
                    f"{_safe_external_error(exc)}"
                ),
                error_type="api_error",
                provider="grsai",
                model=model,
                prompt=prompt,
                aspect_ratio=aspect,
            )


def register(ctx) -> None:
    ctx.register_image_gen_provider(GrsAIImageGenProvider())
