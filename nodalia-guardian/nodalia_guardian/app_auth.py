"""Short-lived installation tokens for the Nodalia Guardian GitHub App.

The private key never leaves the machine and is never sent to Claude.
"""

import base64
import datetime as dt
import json
import urllib.error
import urllib.request
from pathlib import Path


class AppAuthError(RuntimeError):
    pass


def _base64url(payload: bytes) -> str:
    return base64.urlsafe_b64encode(payload).decode("ascii").rstrip("=")


def app_jwt(app_id: int, private_key_pem: bytes, now: int | None = None) -> str:
    """Sign a 9-minute RS256 JWT. Requires the optional `app` dependency."""
    if app_id <= 0 or not private_key_pem:
        raise ValueError("GitHub App ID and PEM private key are required")
    try:
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import padding, rsa
    except ImportError as exc:
        raise AppAuthError("Install the optional dependency: pip install '.[app]'") from exc
    key = serialization.load_pem_private_key(private_key_pem, password=None)
    if not isinstance(key, rsa.RSAPrivateKey):
        raise AppAuthError("GitHub App private key must be RSA")
    current = int(now if now is not None else dt.datetime.now(dt.timezone.utc).timestamp())
    header = _base64url(b'{"alg":"RS256","typ":"JWT"}')
    claims = _base64url(json.dumps({"iat": current - 60, "exp": current + 540, "iss": str(app_id)}, separators=(",", ":")).encode())
    unsigned = f"{header}.{claims}".encode()
    signature = key.sign(unsigned, padding.PKCS1v15(), hashes.SHA256())
    return f"{unsigned.decode()}.{_base64url(signature)}"


def installation_access_token(app_id: int, private_key_path: str | Path, installation_id: int) -> str:
    """Exchange app identity for a time-limited installation token."""
    if installation_id <= 0:
        raise ValueError("Installation ID must be positive")
    pem_path = Path(private_key_path)
    if not pem_path.is_file():
        raise AppAuthError("GitHub App PEM key file does not exist")
    jwt = app_jwt(app_id, pem_path.read_bytes())
    request = urllib.request.Request(
        f"https://api.github.com/app/installations/{installation_id}/access_tokens",
        data=b"{}", method="POST",
        headers={"Accept": "application/vnd.github+json", "Authorization": f"Bearer {jwt}",
                 "X-GitHub-Api-Version": "2022-11-28", "User-Agent": "nodalia-guardian/0.2"},
    )
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            raw = response.read(100_001)
    except urllib.error.HTTPError as exc:
        raise AppAuthError(f"GitHub App token exchange returned HTTP {exc.code}") from None
    except (urllib.error.URLError, TimeoutError):
        raise AppAuthError("GitHub App token exchange could not connect") from None
    try:
        token = json.loads(raw)["token"]
    except (ValueError, KeyError, TypeError):
        raise AppAuthError("Invalid GitHub App token response") from None
    if not isinstance(token, str) or len(token) < 20:
        raise AppAuthError("GitHub App installation token missing")
    return token
