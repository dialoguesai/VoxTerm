"""RFC 7636 PKCE (S256) for Grant Access /connect."""

from __future__ import annotations

import base64
import hashlib
import secrets
from dataclasses import dataclass


@dataclass(frozen=True)
class PkcePair:
    code_verifier: str
    code_challenge: str
    code_challenge_method: str = "S256"


def _base64url_encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def create_pkce_pair(length: int = 64) -> PkcePair:
    alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-._~"
    verifier = "".join(secrets.choice(alphabet) for _ in range(length))
    digest = hashlib.sha256(verifier.encode("utf-8")).digest()
    return PkcePair(code_verifier=verifier, code_challenge=_base64url_encode(digest))
