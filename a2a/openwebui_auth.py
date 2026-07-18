# File: openwebui_auth.py
"""
Helper for authenticating against an OpenWebUI instance and obtaining a
JWT that can be used as a Bearer token for OpenWebUI's OpenAI-compatible
API (`<base>/api/chat/completions`, `<base>/api/models`, ...).
"""
import logging
import os
from typing import Optional

import requests

LOGGER = logging.getLogger(__name__)


def get_openwebui_token(
    base_url: str,
    email: str,
    password: str,
    timeout: float = 10.0,
) -> str:
    """
    Sign in to OpenWebUI and return the JWT access token.

    Tries `/api/v1/auths/signin` first (current OpenWebUI), then falls back
    to `/api/v1/auth/signin` for older versions.
    """
    base_url = base_url.rstrip("/")
    payload = {"email": email, "password": password}

    last_err: Optional[Exception] = None
    for path in ("/api/v1/auths/signin", "/api/v1/auth/signin"):
        url = f"{base_url}{path}"
        try:
            resp = requests.post(url, json=payload, timeout=timeout)
            if resp.status_code == 404:
                continue
            resp.raise_for_status()
            data = resp.json()
            token = data.get("token") or data.get("access_token")
            if not token:
                raise RuntimeError(f"No token in OpenWebUI response: {data}")
            LOGGER.info(f"Authenticated with OpenWebUI at {base_url}")
            return token
        except Exception as e:
            last_err = e
            LOGGER.debug(f"OpenWebUI signin via {url} failed: {e}")

    raise RuntimeError(
        f"Failed to authenticate with OpenWebUI at {base_url}: {last_err}"
    )


def get_token_from_env() -> str:
    """Convenience: read OPENWEBUI_* env vars and obtain a token."""
    base = os.environ.get("OPENWEBUI_BASE_URL")
    email = os.environ.get("OPENWEBUI_EMAIL")
    password = os.environ.get("OPENWEBUI_PASSWORD")
    if not (base and email and password):
        raise RuntimeError(
            "OPENWEBUI_BASE_URL, OPENWEBUI_EMAIL, and OPENWEBUI_PASSWORD "
            "must all be set to use the openwebui provider."
        )
    return get_openwebui_token(base, email, password)
