"""Basic abuse protection for the public API: optional API key + per-IP rate limit.

This is a first line of defence for a demo/pilot, not full authentication. Production needs real
identities (OAuth/OIDC or signed platform tokens) and per-tenant quotas: see docs/SECURITY_PRIVACY.md.
"""
import secrets
import time

from fastapi import Depends, HTTPException, Request

from app.config import get_settings
from app.job_store import JobStore, get_job_store

RATE_PREFIX = "agk:rl:"


def client_ip(request: Request) -> str:
    """The caller's IP. X-Forwarded-For is only trusted when we run behind our own proxy."""
    if get_settings().trust_proxy_headers:
        forwarded = request.headers.get("x-forwarded-for", "")
        if forwarded:
            return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def require_api_key(request: Request) -> None:
    """If API_KEY is configured, write requests must send it in the X-API-Key header."""
    expected = get_settings().api_key
    if not expected:
        return
    given = request.headers.get("x-api-key", "")
    if not secrets.compare_digest(given.encode(), expected.encode()):
        raise HTTPException(status_code=401, detail="Missing or invalid API key")


def rate_limit(request: Request, store: JobStore = Depends(get_job_store)) -> None:
    """Fixed one-minute window per client IP, shared by all API processes through Redis."""
    limit = get_settings().rate_limit_per_minute
    if limit <= 0:
        return
    window = int(time.time() // 60)
    key = f"{RATE_PREFIX}{client_ip(request)}:{window}"
    with store.r.pipeline() as pipe:
        pipe.incr(key)
        pipe.expire(key, 90)
        count, _ = pipe.execute()
    if count > limit:
        retry_after = 60 - int(time.time() % 60)
        raise HTTPException(status_code=429, detail="Too many requests. Please wait a minute and try again.",
                            headers={"Retry-After": str(retry_after)})
