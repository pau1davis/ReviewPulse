import hashlib
import hmac

import httpx
from jose import JWTError, jwk, jwt
from jose.exceptions import ExpiredSignatureError

from app.core.config import settings

# In-memory cache for Supabase JWKS public keys (they rarely rotate)
_jwks_cache: list[dict] | None = None


def _get_jwks_keys() -> list[dict]:
    global _jwks_cache
    if _jwks_cache is None:
        url = f"{settings.supabase_url.strip()}/auth/v1/.well-known/jwks.json"
        resp = httpx.get(url, timeout=5)
        resp.raise_for_status()
        _jwks_cache = resp.json().get("keys", [])
    return _jwks_cache


# ── JWT verification ───────────────────────────────────────────────────────────

def verify_supabase_jwt(token: str) -> dict:
    """
    Decode and verify a Supabase Auth JWT.
    Supports HS256 (legacy projects) and ES256 (new projects, asymmetric signing).
    """
    try:
        header = jwt.get_unverified_header(token)
        alg = header.get("alg", "HS256")

        if alg == "HS256":
            payload = jwt.decode(
                token,
                settings.supabase_jwt_secret,
                algorithms=["HS256"],
                audience="authenticated",
            )
        else:
            # ES256: verify with Supabase's public key from JWKS endpoint
            kid = header.get("kid")
            keys = _get_jwks_keys()
            key_data = next((k for k in keys if k.get("kid") == kid), None)
            if not key_data:
                raise ValueError(f"No JWKS key found for kid={kid!r}")
            public_key = jwk.construct(key_data, algorithm="ES256")
            payload = jwt.decode(
                token,
                public_key,
                algorithms=["ES256"],
                audience="authenticated",
            )

        return payload
    except ExpiredSignatureError:
        raise ValueError("Token has expired.")
    except JWTError as exc:
        raise ValueError(f"Invalid token: {exc}")


def extract_supabase_user_id(token: str) -> str:
    """Verify the JWT and return the Supabase user ID (the `sub` claim)."""
    payload = verify_supabase_jwt(token)
    user_id = payload.get("sub")
    if not user_id:
        raise ValueError("Token is missing the 'sub' claim.")
    return user_id



# ── Webhook signature ──────────────────────────────────────────────────────────

def verify_webhook_signature(payload_bytes: bytes, signature_header: str) -> bool:
    """
    Verify an HMAC-SHA256 webhook signature produced by ReviewPulse.

    Signature scheme:
      1. Canonical JSON body (no extra whitespace) is HMAC-SHA256'd with
         the WEBHOOK_SECRET environment variable as the key.
      2. The digest is hex-encoded and sent as:
             X-ReviewPulse-Signature: sha256=<hex_digest>

    Receiver verification example (Python):
        body = await request.body()
        header = request.headers.get("X-ReviewPulse-Signature", "")
        if not verify_webhook_signature(body, header):
            raise HTTPException(status_code=401, detail="Invalid signature")

    Uses constant-time comparison (hmac.compare_digest) to prevent
    timing-based signature forgery.
    """
    if not signature_header.startswith("sha256="):
        return False

    provided_sig = signature_header[len("sha256="):]
    expected_sig = hmac.new(
        settings.webhook_secret.encode("utf-8"),
        payload_bytes,
        hashlib.sha256,
    ).hexdigest()

    return hmac.compare_digest(provided_sig, expected_sig)
