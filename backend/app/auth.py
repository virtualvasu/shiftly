"""JWT identity. Company, employee and role come only from a verified token."""

import hashlib
import hmac
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Literal

import jwt
from fastapi import Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.config import Settings

ALGORITHM = "HS256"
SESSION_EXPIRED = "Your session has expired. Please sign in again."
NOT_ALLOWED = "This action isn't available for your account."


@dataclass(frozen=True)
class TenantContext:
    company_id: str
    employee_id: str
    role: Literal["employee", "manager"]


class AuthError(Exception):
    pass


_SCRYPT = {"n": 2**14, "r": 8, "p": 1, "dklen": 32}


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, **_SCRYPT)
    return f"scrypt${salt.hex()}${digest.hex()}"


def verify_password(password: str, stored: str | None) -> bool:
    try:
        scheme, salt_hex, digest_hex = (stored or "").split("$")
    except ValueError:
        return False
    if scheme != "scrypt":
        return False
    digest = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt_hex), **_SCRYPT)
    return hmac.compare_digest(digest.hex(), digest_hex)


def issue_token(ctx: TenantContext, settings: Settings, now: datetime) -> str:
    claims = {
        "sub": ctx.employee_id,
        "company_id": ctx.company_id,
        "role": ctx.role,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(minutes=settings.jwt_ttl_min)).timestamp()),
    }
    return jwt.encode(claims, settings.jwt_secret, algorithm=ALGORITHM)


def decode_token(token: str, settings: Settings, now: datetime) -> TenantContext:
    # Expiry is checked against the app's own clock (the same one that issued
    # the token), not the wall clock, so demo and test clocks stay consistent.
    try:
        claims = jwt.decode(
            token,
            settings.jwt_secret,
            algorithms=[ALGORITHM],
            options={
                "require": ["sub", "company_id", "role", "exp"],
                "verify_exp": False,
                "verify_iat": False,
            },
        )
    except jwt.PyJWTError as e:
        raise AuthError(str(e)) from e
    if now.timestamp() >= claims["exp"]:
        raise AuthError("expired")
    if claims["role"] not in ("employee", "manager"):
        raise AuthError("bad role")
    return TenantContext(claims["company_id"], claims["sub"], claims["role"])


_bearer = HTTPBearer(auto_error=False)


def current_ctx(
    request: Request, creds: HTTPAuthorizationCredentials | None = Depends(_bearer)
) -> TenantContext:
    if creds is None:
        raise HTTPException(status_code=401, detail=SESSION_EXPIRED)
    try:
        services = request.app.state.services
        return decode_token(creds.credentials, services.settings, services.clock.now())
    except AuthError:
        raise HTTPException(status_code=401, detail=SESSION_EXPIRED)


def require_employee(ctx: TenantContext = Depends(current_ctx)) -> TenantContext:
    if ctx.role != "employee":
        raise HTTPException(status_code=403, detail=NOT_ALLOWED)
    return ctx


def require_manager(ctx: TenantContext = Depends(current_ctx)) -> TenantContext:
    if ctx.role != "manager":
        raise HTTPException(status_code=403, detail=NOT_ALLOWED)
    return ctx
