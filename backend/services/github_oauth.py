"""GitHub identity lookup and short-lived, single-use OAuth credentials."""

import base64
import hashlib
import json
import re
import secrets
from functools import lru_cache
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import redis
import requests
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from backend.core.config import settings
from backend.infrastructure.database.models import User

FLOW_TTL = 600
TICKET_TTL = 60
FRONTEND_PATHS = {"/", "/account", "/admin", "/workspace/knowledges", "/services/knowledge", "/try"}


class OAuthError(Exception):
    def __init__(self, reason: str):
        self.reason = reason
        super().__init__(reason)


@lru_cache(maxsize=1)
def oauth_redis():
    # Authentication must fail closed; the general cache deliberately swallows errors.
    return redis.Redis.from_url(settings.redis_url, decode_responses=True,
                               socket_connect_timeout=3, socket_timeout=3)


def store_credential(kind: str, value: str, browser: str, payload: dict, ttl: int):
    try:
        oauth_redis().setex(credential_key(kind, value, browser), ttl, json.dumps(payload))
    except redis.RedisError:
        raise OAuthError("unavailable") from None


def consume_credential(kind: str, value: str, browser: str) -> dict:
    if not value or not browser or len(value) > 128 or len(browser) > 128:
        raise OAuthError("expired")
    try:
        raw = oauth_redis().getdel(credential_key(kind, value, browser))
    except redis.RedisError:
        raise OAuthError("unavailable") from None
    if not raw:
        raise OAuthError("expired")
    return json.loads(raw)


def credential_key(kind: str, value: str, browser: str) -> str:
    digest = hashlib.sha256(f"{value}.{browser}".encode()).hexdigest()
    return f"{settings.redis_key_prefix}:github_oauth:{kind}:{digest}"


def public_origin() -> str:
    uri = urlsplit(settings.github_redirect_uri)
    return urlunsplit((uri.scheme, uri.netloc, "", "", ""))


def safe_next(value: str) -> str:
    # Exact path allowlist also rejects protocol-relative redirects and control characters.
    if len(value) > 2048 or any(ord(char) < 32 or char == "\\" for char in value):
        return "/"
    try:
        uri = urlsplit(value)
    except ValueError:
        return "/"
    if uri.scheme or uri.netloc or uri.path not in FRONTEND_PATHS:
        return "/"
    query = [(key, item) for key, item in parse_qsl(uri.query)
             if key not in {"github", "github_error"}]
    return urlunsplit(("", "", uri.path, urlencode(query), ""))


def frontend_result(next_path: str, reason: str | None = None) -> str:
    uri = urlsplit(safe_next(next_path))
    query = parse_qsl(uri.query)
    query.append(("github_error", reason) if reason else ("github", "success"))
    return public_origin() + urlunsplit(("", "", uri.path, urlencode(query), ""))


def begin_login(next_path: str) -> tuple[str, str]:
    state, browser, verifier = (secrets.token_urlsafe(32) for _ in range(3))
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    store_credential("state", state, browser, {"verifier": verifier, "next": safe_next(next_path)}, FLOW_TTL)
    # Public profile only. No repository or private email permission is requested.
    query = urlencode({"client_id": settings.github_client_id,
                       "redirect_uri": settings.github_redirect_uri, "state": state,
                       "scope": "", "code_challenge": challenge, "code_challenge_method": "S256"})
    return "https://github.com/login/oauth/authorize?" + query, browser


def fetch_github_profile(code: str, verifier: str) -> dict:
    try:
        with requests.Session() as client:
            response = client.post("https://github.com/login/oauth/access_token", data={
                "client_id": settings.github_client_id, "client_secret": settings.github_client_secret,
                "code": code, "code_verifier": verifier, "redirect_uri": settings.github_redirect_uri,
            }, headers={"Accept": "application/json"}, timeout=(5, 10), allow_redirects=False)
            if response.status_code != 200:
                raise OAuthError("failed")
            data = response.json()
            access_token = data.get("access_token") if isinstance(data, dict) else None
            if not isinstance(access_token, str) or not access_token or data.get("error"):
                raise OAuthError("failed")
            response = client.get("https://api.github.com/user", headers={
                "Authorization": f"Bearer {access_token}", "Accept": "application/vnd.github+json",
                "User-Agent": "SuperMew-GitHub-Login",
            }, timeout=(5, 10), allow_redirects=False)
            if response.status_code != 200:
                raise OAuthError("failed")
            profile = response.json()
    except (requests.RequestException, ValueError):
        # Never include provider responses, tokens or request objects in errors/logs.
        raise OAuthError("unavailable") from None
    if (not isinstance(profile, dict) or type(profile.get("id")) is not int
            or not 0 < profile["id"] < 10**32
            or not isinstance(profile.get("login"), str)
            or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9-]{0,38}", profile["login"])):
        raise OAuthError("failed")
    return profile


def github_user(db: Session, profile: dict) -> User:
    github_id = str(profile["id"])
    existing = db.query(User).filter(User.github_id == github_id).first()
    if existing:
        return existing
    base_name = f"gh_{profile['login']}_{github_id}"
    for _ in range(3):
        name = base_name
        if db.query(User.id).filter(User.username == name).first():
            name += "_" + secrets.token_hex(4)
        # '!' cannot be verified as any supported password hash: OAuth-only accounts
        # have no password login. Never attach an existing same-name account.
        user = User(username=name, github_id=github_id, password_hash="!", role="user")
        db.add(user)
        try:
            db.commit()
            return user
        except IntegrityError:
            db.rollback()
            existing = db.query(User).filter(User.github_id == github_id).first()
            if existing:
                return existing
    raise OAuthError("failed")
